import ast
from concurrent import futures
import logging
import time
import timeit

import grpc
import six

from django.core.exceptions import ObjectDoesNotExist
from django.db import connection

# This import is important! It's here so jobChain is imported before
# linkTaskManagerChoice is imported. The old RPCServer module was doing the
# same but it was not documented. This is easy to fix but it requires some
# refactoring.
import archivematicaMCP

from main import models
from protos import mcpserver_pb2


logger = logging.getLogger("archivematica.mcp.server.grpc")

_ONE_DAY_IN_SECONDS = 60 * 60 * 24


def _log_rpc(func):
    def _decorated(*args, **kwargs):
        logger.debug("rpc %s | Request received", func.func_name)
        start_time = timeit.default_timer()
        request = args[1]
        for name, _ in six.iteritems(request.DESCRIPTOR.fields_by_camelcase_name):
            # Unpacked, but I don't need the `google.protobuf.descriptor.FieldDescriptor` for now
            try:
                value = getattr(request, name)
            except AttributeError:
                logger.debug("rpc %s | Parameter %s received but it is unknown? (type %s)", func.func_name, name, type(value).__name__)
            else:
                logger.debug("rpc %s | Parameter %s received (type %s)", func.func_name, name, type(value).__name__)
        try:
            return func(*args, **kwargs)
        finally:
            elapsed = 1000 * (timeit.default_timer() - start_time)
            logger.debug("rpc %s | Response sent (processing time: %.2fms)", func.func_name, elapsed)
    return _decorated


# Map the internal linkTaskManager unitType attribute to our proto values
_UNIT_TYPES = {
    'SIP': mcpserver_pb2.JobListResponse.Job.UnitType.Value('INGEST'),
    'Transfer': mcpserver_pb2.JobListResponse.Job.UnitType.Value('TRANSFER'),
    'DIP': mcpserver_pb2.JobListResponse.Job.UnitType.Value('DIP'),
}


_TASK_TYPES = {
    '36b2e239-4a57-4aa5-8ebc-7a29139baca6': (mcpserver_pb2.Link.LinkType.Value('RUN_ONCE'), models.StandardTaskConfig),
    'a6b1c323-7d36-428e-846a-e7e819423577': (mcpserver_pb2.Link.LinkType.Value('RUN_FOR_EACH_FILE'), models.StandardTaskConfig),
    '61fb3874-8ef6-49d3-8a2d-3cb66e86a30c': (mcpserver_pb2.Link.LinkType.Value('GET_USER_CHOICE_CHAIN'), models.MicroServiceChainChoice),
    '9c84b047-9a6d-463f-9836-eafa49743b84': (mcpserver_pb2.Link.LinkType.Value('GET_USER_CHOICE_DICT'), models.MicroServiceChoiceReplacementDic),
    'a19bfd9f-9989-4648-9351-013a10b382ed': (mcpserver_pb2.Link.LinkType.Value('GENERATE_USER_CHOICE'), models.StandardTaskConfig),
    '01b748fe-2e9d-44e4-ae5d-113f74c9a0ba': (mcpserver_pb2.Link.LinkType.Value('GET_GENERATED_USER_CHOICE'), models.StandardTaskConfig),
    'c42184a3-1a7f-4c4d-b380-15d8d97fdd11': (mcpserver_pb2.Link.LinkType.Value('GET_UNIT_VARIABLE'), models.TaskConfigUnitVariableLinkPull),
    '6f0b612c-867f-4dfd-8e43-5b35b7f882d7': (mcpserver_pb2.Link.LinkType.Value('SET_UNIT_VARIABLE'), models.TaskConfigSetUnitVariable),
    '6fe259c2-459d-4d4b-81a4-1b9daf7ee2e9': (mcpserver_pb2.Link.LinkType.Value('GET_MAGIC_LINK'), None),
    '3590f73d-5eb0-44a0-91a6-5b2db6655889': (mcpserver_pb2.Link.LinkType.Value('SET_MAGIC_LINK'), None),
}


def get_link_details(id_):
    """
    Serialize chain links into a mcpserver_pb2.Link. At the moment, this
    implementaiton is is incomplete because it does not care about all the
    configuration types that exist but only those that I need at the moment.
    """
    try:
        mscl = models.MicroServiceChainLink.objects.get(id=id_)
        task_config = mscl.currenttask
    except ObjectDoesNotExist:
        logger.warning('Link %s not found or it is missing its configuration', id_)
        return None
    link = mcpserver_pb2.Link(
        id=mscl.id,
        group=mscl.microservicegroup,
        defaultStatus=int(mscl.defaultexitmessage),
        defaultLinkId=mscl.defaultnextchainlink_id,
        description=task_config.description,
    )
    link.type = _TASK_TYPES[task_config.tasktype_id][0] if task_config.tasktype_id in _TASK_TYPES else 0
    for item in models.MicroServiceChainLinkExitCode.objects.filter(microservicechainlink_id=mscl.id):
        ec = link.exitCodes.add()
        ec.code = item.exitcode
        ec.status = int(item.exitmessage)
        ec.linkId = item.nextmicroservicechainlink_id

    if link.type == mcpserver_pb2.Link.LinkType.Value('GET_USER_CHOICE_CHAIN'):
        for item in models.MicroServiceChainChoice.objects.filter(choiceavailableatlink_id=mscl.id):
            choice = link.config.mscc.choices.add()
            choice.chainId = item.chainavailable_id
            choice.description = item.chainavailable.description

    elif link.type == mcpserver_pb2.Link.LinkType.Value('GET_USER_CHOICE_DICT'):
        for item in models.MicroServiceChoiceReplacementDic.objects.filter(choiceavailableatlink_id=mscl.id):
            dict_ = link.config.mscrd.dicts.add()
            dict_.id = item.id
            dict_.description = item.description
            try:
                rd = ast.literal_eval(item.replacementdic)
            except (SyntaxError, ValueError):
                continue
            for key, value in rd.items():
                dict_.items[key.strip('%')] = value

    return link


def get_chain_details(id_):
    try:
        msc = models.MicroServiceChain.objects.get(id=id_)
    except ObjectDoesNotExist:
        logger.warning('Link %s not found or it is missing its configuration', id_)
        return None
    chain = mcpserver_pb2.Chain(
        id=msc.id,
        description=msc.description,
        linkId=msc.startinglink_id,
    )
    return chain


class gRPCServer(object):
    def __init__(self, unit_choices):
        self.unit_choices = unit_choices

    @_log_rpc
    def ApproveTransfer(self, request, context):
        """
        Look up the transfer given its UUID. Proceed only if the choice is a
        'Approve transfer'.
        """
        resp = mcpserver_pb2.ApproveTransferResponse()
        with self.unit_choices:
            for job_uuid, task_manager in self.unit_choices.items():
                unit_uuid = task_manager.unit.UUID
                if request.id != unit_uuid:
                    continue
                for item in task_manager.choices:
                    value = item[0]
                    description = item[1]
                    if description != 'Approve transfer':
                        continue
                    match = task_manager
                    break
            try:
                match
            except NameError:
                context.set_code(grpc.StatusCode.NOT_FOUND)
            else:
                match.proceedWithChoice(value, user_id=None)
                resp.approved = True
        return resp

    @_log_rpc
    def ApproveJob(self, request, context):
        """
        Approve the job requested only if avaiable.
        """
        resp = mcpserver_pb2.ApproveJobResponse()
        with self.unit_choices:
            if request.id not in self.unit_choices:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                return resp
            self.unit_choices.get(request.id).proceedWithChoice(request.choiceId, user_id=None)
        resp.approved = True
        return resp

    @_log_rpc
    def JobList(self, request, context):
        resp = mcpserver_pb2.JobListResponse(transferCount=0, ingestCount=0)

        units_visited = {'Transfer': [], 'SIP': [], 'DIP': []}
        with self.unit_choices:
            for job_uuid, task_manager in self.unit_choices.items():
                if task_manager.unit.unitType not in ('Transfer', 'SIP', 'DIP'):
                    continue

                try:
                    unit_type = 'SIP' if task_manager.unit.unitType == 'DIP' else task_manager.unit.unitType
                    unit = getattr(models, unit_type).objects.get(pk=task_manager.unit.UUID)
                except ObjectDoesNotExist:
                    # Omit if it's not in the database
                    continue
                else:
                    # Omit hidden
                    if task_manager.unit.unitType in ('Transfer', 'DIP') and unit.hidden:
                        continue

                units_visited[task_manager.unit.unitType].append(task_manager.unit.UUID)

                # Each element in the global shared dictionary represents a job
                # with a set of choices. These choices are structured differently
                # based on the type of task manager.
                job = resp.jobs.add()
                job.id = job_uuid
                job.unitType = _UNIT_TYPES.get(task_manager.unit.unitType)
                for item in task_manager.choices:
                    choice = job.choices.add()
                    choice.description = item[1]
                    choice.value = str(item[0])

            # TODO: make sure that DIPs don't add up to the total. At the
            # moment, I don't think I can pull that detail fast enough to be
            # part of a RPC.
            resp.transferCount = len(units_visited['Transfer'])
            resp.ingestCount = len(units_visited['SIP'])

        return resp

    @_log_rpc
    def ListMicroserviceChoiceReplacements(self, request, context):
        resp = mcpserver_pb2.ListMicroserviceChoiceReplacementsResponse()
        filter_params = {}
        if request.microserviceId:
            filter_params['choiceavailableatlink_id'] = request.microserviceId
        if request.description:
            filter_params['description'] = request.description
        for item in models.MicroServiceChoiceReplacementDic.objects.filter(**filter_params):
            repl = resp.replacements.add()
            repl.microserviceId = item.choiceavailableatlink.id
            repl.description = item.description
            try:
                args = ast.literal_eval(item.replacementdic)
            except (ValueError, SyntaxError):
                continue
            else:
                for k, v in six.iteritems(args):
                    repl.arguments[k] = v
        return resp

    @_log_rpc
    def LinkGet(self, request, context):
        resp = mcpserver_pb2.LinkGetResponse()
        link = get_link_details(request.id)
        if link is None:
            logger.debug('Link %s not found', request.id)
            context.set_code(grpc.StatusCode.NOT_FOUND)
        else:
            resp.link.CopyFrom(link)  # get_link_details creates its own Link message
        return resp

    @_log_rpc
    def ChainGet(self, request, context):
        resp = mcpserver_pb2.ChainGetResponse()
        chain = get_chain_details(request.id)
        if chain is None:
            logger.debug('Chain %s not found', request.id)
            context.set_code(grpc.StatusCode.NOT_FOUND)
        else:
            resp.chain.CopyFrom(chain)  # get_chain_details creates its own Chain message
        return resp

    @_log_rpc
    def ListMicroserviceChoiceDuplicates(self, request, context):
        resp = mcpserver_pb2.ListMicroserviceChoiceDuplicatesResponse()
        if not all((request.linkName, request.choiceName)):
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            return resp
        sql = """
            SELECT
                MicroServiceChainLinks.pk,
                MicroServiceChains.pk
            FROM TasksConfigs
            LEFT JOIN MicroServiceChainLinks ON (MicroServiceChainLinks.currentTask = TasksConfigs.pk)
            LEFT JOIN MicroServiceChainChoice ON (MicroServiceChainChoice.choiceAvailableAtLink = MicroServiceChainLinks.pk)
            LEFT JOIN MicroServiceChains ON (MicroServiceChains.pk = MicroServiceChainChoice.chainAvailable)
            WHERE
                TasksConfigs.description = %s
                AND MicroServiceChains.description = %s;
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [request.linkName, request.choiceName])
            for item in cursor:
                dup = resp.duplicates.add()
                dup.srcId = item[0]
                dup.dstId = item[1]
        logger.debug("Duplicates found: %d", len(resp.duplicates))
        return resp


def start(unit_choices):
    """
    Start our gRPC server with which our RPCs can be serviced. We pass our own
    pool of threads (futures.ThreadPoolExecutor) that we want the server to use
    to service the RPCs.
    """
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=5))
    mcpserver_pb2.add_MCPServerServicer_to_server(gRPCServer(unit_choices), server)

    # We can afford to do this as long as Archivematica runs in a box.
    # Hoping not to do that for much longer though.
    addr = '[::]:50051'
    server.add_insecure_port(addr)
    logger.info('gRPC server listening on %s', addr)

    server.start()
    try:
        while True:
            time.sleep(_ONE_DAY_IN_SECONDS)
    except KeyboardInterrupt:
        server.stop(0)
