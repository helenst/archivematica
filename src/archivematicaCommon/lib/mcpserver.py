import os

import grpc

import protos.mcpserver_pb2


class Client(object):
    """
    MCPServer client using the gRPC protocol.
    """
    def __init__(self, address='localhost:50051'):
        self.channel = grpc.insecure_channel(address)
        self.stub = protos.mcpserver_pb2.MCPServerStub(self.channel)

    def approve_transfer(self, sip_uuid):
        resp = self.stub.ApproveTransfer(protos.mcpserver_pb2.ApproveTransferRequest(id=sip_uuid))
        return resp.approved

    def approve_job(self, job_uuid, choice_id):
        resp = self.stub.ApproveJob(protos.mcpserver_pb2.ApproveJobRequest(id=job_uuid, choiceId=choice_id))
        return resp.approved

    def list_jobs(self):
        return self.stub.JobList(protos.mcpserver_pb2.JobListRequest())

    def get_link(self, id_):
        try:
            return self.stub.LinkGet(protos.mcpserver_pb2.LinkGetRequest(id=id_)).link
        except grpc._channel._Rendezvous as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                return None
            raise

    def get_chain(self, id_):
        try:
            return self.stub.ChainGet(protos.mcpserver_pb2.ChainGetRequest(id=id_)).chain
        except grpc._channel._Rendezvous as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                return None
            raise

    def list_microservice_choice_duplicates(self, link_name, choice_name):
        request = protos.mcpserver_pb2.ListMicroserviceChoiceDuplicatesRequest(linkName=link_name, choiceName=choice_name)
        return self.stub.ListMicroserviceChoiceDuplicates(request).duplicates


"""
PLEASE IGNORE THIS! IT IS JUST A CLIENT FOR TESTING PURPOSES!
"""
if __name__ == '__main__':

    import sys

    def usage(commands):
        script_name = os.path.basename(__file__)
        sys.exit('{} [{}]'.format(script_name, '|'.join(commands)))

    commands = [
        'JobList',
        'ApproveJob',
        'ApproveTransfer',
        'LinkGet',
        'ChainGet',
        'ListMicroserviceChoiceDuplicates',
    ]

    if len(sys.argv) == 1:
        usage(commands)

    cmd = sys.argv[1]
    client = Client()

    if cmd not in commands:
        usage(commands)

    if cmd == 'JobList':
        resp = client.list_jobs()
        jobs = resp.jobs
        print("Pending choices: [ Transfer = {} ] [ Ingest = {} ]".format(resp.transferCount, resp.ingestCount))
        for job in jobs:
            print("\nJob {} of unit with type {}".format(job.id, protos.mcpserver_pb2.JobListResponse.Job.UnitType.Name(job.unitType)))
            for item in job.choices:
                print("\tChoice: {} (value={})".format(item.description, item.value))

    elif cmd == 'ApproveJob':
        try:
            job_uuid = sys.argv[2]
            choice_uuid = sys.argv[3]
        except IndexError:
            sys.exit('Missing parameters (job_uuid, choice_uuid)')
        approved = client.approve_job(job_uuid, choice_uuid)
        print("Has the job been approved? {}".format("Yes!" if approved else "No, :("))

    elif cmd == 'ApproveTransfer':
        try:
            sip_uuid = sys.argv[2]
        except IndexError:
            sys.exit('Missing parameter (sip_uuid)')
        approved = client.approve_transfer(sip_uuid)
        print("Has the transfer been approved? {}".format("Yes!" if approved else "No, :("))

    elif cmd == 'LinkGet':
        try:
            id_ = sys.argv[2]
        except IndexError:
            sys.exit('Missing parameter (id)')
        link = client.get_link(id_)
        if link is None:
            print("Link %s not found!" % id_)
        print(link)

    elif cmd == 'ChainGet':
        try:
            id_ = sys.argv[2]
        except IndexError:
            sys.exit('Missing parameter (id)')
        chain = client.get_chain(id_)
        if chain is None:
            print("Chain %s not found!" % id_)
        print(chain)

    elif cmd == 'ListMicroserviceChoiceDuplicates':
        try:
            link_name = sys.argv[2]
            choice_name = sys.argv[3]
        except IndexError:
            sys.exit('Missing parameter (link_name, choice_name)')
        print(client.list_microservice_choice_duplicates(link_name, choice_name))
