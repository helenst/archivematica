#!/usr/bin/env python3

# This file is part of Archivematica.
#
# Copyright 2010-2013 Artefactual Systems Inc. <http://artefactual.com>
#
# Archivematica is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Archivematica is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Archivematica.  If not, see <http://www.gnu.org/licenses/>.

# @package Archivematica
# @subpackage archivematicaClientScript
# @author Joseph Perry <joseph@artefactual.com>

from __future__ import print_function
from __future__ import unicode_literals
from builtins import str
import os
import sys
import uuid

import django
django.setup()
# dashboard
from main.models import Event

# archivematicaCommon
from custom_handlers import get_script_logger
from executeOrRunSubProcess import executeOrRun
from databaseFunctions import insertIntoEvents

if __name__ == '__main__':
    logger = get_script_logger("archivematica.mcp.client.clamscan")
    fileUUID = sys.argv[1]
    target = sys.argv[2]
    date = sys.argv[3]

    # Check if scan event already exists for this file - if so abort early
    count = Event.objects.filter(file_uuid_id=fileUUID, event_type='virus check').count()
    if count >= 1:
        print('Virus scan already performed, not running scan again')
        sys.exit(0)

    command = ['clamdscan', '-']
    print('Clamscan command:', ' '.join(command), '<', target)
    with open(target) as file_:
        scan_rc, scan_stdout, scan_stderr = executeOrRun("command", command, printing=False, stdIn=file_)
    commandVersion = "clamdscan -V"
    print('Clamscan version command:', commandVersion)
    version_rc, version_stdout, version_stderr = executeOrRun("command", commandVersion, printing=False)

    eventOutcome = "Pass"
    if scan_rc or version_rc:  # Either command returned non-0 RC
        if version_rc:
            print('Error determining version, aborting', file=sys.stderr)
            print('Version RC:', version_rc, file=sys.stderr)
            print('Version Standard output:', version_stdout, file=sys.stderr)
            print('Version Standard error:', version_stderr, file=sys.stderr)
            sys.exit(2)
        else:
            eventOutcome = "Fail"

    clamscanResultShouldBe = "Infected files: 0"
    if eventOutcome == "Fail" or scan_stdout.find(clamscanResultShouldBe) == -1:
        eventOutcome = "Fail"
        print('Scan failed for file', fileUUID, " - ", os.path.basename(target), file=sys.stderr)
        print('Clamscan RC:', scan_rc, file=sys.stderr)
        print('Clamscan Standard output:', scan_stdout, file=sys.stderr)
        print('Clamscan Standard error:', scan_stderr, file=sys.stderr)

    version, virusDefs, virusDefsDate = version_stdout.split("/")
    virusDefs = virusDefs + "/" + virusDefsDate
    eventDetailText = 'program="Clam AV"; version="' + version + '"; virusDefinitions="' + virusDefs + '"'

    print('Event outcome:', eventOutcome)
    if fileUUID != "None":
        insertIntoEvents(fileUUID=fileUUID, eventIdentifierUUID=str(uuid.uuid4()), eventType="virus check", eventDateTime=date, eventDetail=eventDetailText, eventOutcome=eventOutcome, eventOutcomeDetailNote="")
    if eventOutcome != "Pass":
        sys.exit(3)
