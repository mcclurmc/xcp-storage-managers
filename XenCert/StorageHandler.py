#!/usr/bin/python
#
# Copyright (c) 2010 Citrix Systems, Inc. All use and distribution of this
# copyrighted material is governed by and subject to terms and conditions
# as licensed by Citrix Systems, Inc. All other rights reserved.
# Xen, XenSource and XenEnterprise are either registered trademarks or
# trademarks of Citrix Systems, Inc. in the United States and/or other countries.
#

"""Storage handler classes for various storage drivers"""
import sys
import StorageHandlerUtil
from StorageHandlerUtil import Print
from StorageHandlerUtil import PrintOnSameLine
from StorageHandlerUtil import XenCertPrint
from StorageHandlerUtil import displayOperationStatus
import scsiutil, iscsilib
import XenAPI
import util
import glob
from threading import Thread
import time
import os
import ISCSISR
import random
import nfs
import commands

retValIO = 0
timeTaken = '' 
bytesCopied = ''
speedOfCopy = ''
pathsFailed = False
failoverTime = 0

# Hardcoded time limit for Functional tests in hours
timeLimitFunctional = 4

class TimedDeviceIO(Thread):
    def __init__(self, device):
        Thread.__init__(self)
        self.device = device

    def run(self):
        # Sleep for a period of time before checking for any incomplete snapshots to clean.
        devicename = '/dev/' + self.device
        ddOutFile = 'of=' + devicename
        XenCertPrint("Now copy data from /dev/zero to this device and record the time taken to copy it." )
        cmd = ['dd', 'if=/dev/zero', ddOutFile, 'bs=1M', 'count=1', 'oflag=direct']
        try:
	    global retValIO
	    global bytesCopied
            global timeTaken
            global speedOfCopy
	    retValIO = 0
	    timeTaken = '' 
	    bytesCopied = ''
	    speedOfCopy = ''

            (retValIO, stdout, stderr) = util.doexec(cmd,'')
	    if retValIO != 0:
		raise Exception("Disk IO failed for device: %s." % self.device)
            list = stderr.split('\n')
            
            bytesCopied = list[2].split(',')[0]
            timeTaken = list[2].split(',')[1]
            speedOfCopy = list[2].split(',')[2]

            XenCertPrint("The IO test returned rc: %s stdout: %s, stderr: %s" % (retValIO, stdout, list))
        except Exception, e:
                XenCertPrint("Could not write through the allocated disk space on test disk, please check the storage configuration manually. Exception: %s" % str(e))

class WaitForFailover(Thread):
    def __init__(self, session, scsiid, activePaths, noOfPaths):
        Thread.__init__(self)	
	self.scsiid = scsiid
        self.activePaths = activePaths
	self.noOfPaths = noOfPaths

    def run(self):
	# Here wait for the expected number of paths to fail.	
	active = 0
	global pathsFailed
	global failoverTime
	pathsFailed = False
	failoverTime = 0	
	while not pathsFailed and failoverTime < 50:
	    try:
		(retVal, listPathConfigNew) = StorageHandlerUtil.get_path_status(self.scsiid, True)
		if self.noOfPaths == ((int)(self.activePaths) - len(listPathConfigNew)):
		    pathsFailed = True
		time.sleep(1)
		failoverTime += 1		
	    except Exception, e:		
		raise Exception(e)
	    
class StorageHandler:
    def __init__(self, storage_conf):
	XenCertPrint("Reached Storagehandler constructor")
	self.storage_conf = storage_conf
        self.session = util.get_localAPI_session()
        
    def ControlPathStressTests(self):
        sr_ref = None 
	retVal = True
	checkPoint = 0
	totalCheckPoints = 5
	pbdPlugUnplugCount = 10
	
        try:
	    Print("SR CREATION, PBD PLUG-UNPLUG AND SR DELETION TESTS")
	    Print(">> These tests verify the control path by creating an SR, unplugging")
	    Print("   and plugging the PBDs and destroying the SR in multiple iterations.")
	    Print("")
	    
            for i in range(0, 10):
                Print("   -> Iteration number: %d" % i)
		totalCheckPoints += (2 + pbdPlugUnplugCount)
		(retVal, sr_ref, device_config) = self.Create()
		if not retVal:		    
		    raise Exception("      SR creation failed.")
		else:
		    checkPoint += 1
	        
		 # Plug and unplug the PBD over multiple iterations
		checkPoint += StorageHandlerUtil.PlugAndUnplugPBDs(self.session, sr_ref, pbdPlugUnplugCount)
		
		# destroy the SR
		Print("      Destroy the SR.")
		StorageHandlerUtil.DestroySR(self.session, sr_ref)
		checkPoint += 1
	            
            Print("SR SPACE AVAILABILITY TEST")
	    Print(">> This test verifies that all the free space advertised by an SR")
	    Print("   is available and writable.")
	    Print("")

            # Create and plug the SR and create a VDI of the maximum space available. Plug the VDI into Dom0 and write data across the whole virtual disk.
            Print("   Create a new SR.")
	    try:
		(retVal, sr_ref, device_config) = self.Create()
		if not retVal:		    
		    raise Exception("      SR creation failed.")
		else:
		    checkPoint += 1
                
		XenCertPrint("Created the SR %s using device_config: %s" % (sr_ref, device_config))
	        displayOperationStatus(True)
	    except Exception, e:
		displayOperationStatus(False)
		raise e
            	    
	    (checkPointDelta, retVal) = StorageHandlerUtil.PerformSRControlPathTests(self.session, sr_ref)
	    if not retVal:		
		raise Exception("PerformSRControlPathTests failed. Please check the logs for details.")
            else:
                checkPoint += checkPointDelta

	except Exception, e: 
 	    Print("- Control tests failed with an exception.")
	    Print("  Exception: %s" % str(e))
            displayOperationStatus(False)
	    retVal = False

        try:
	    # Try cleaning up here
            if sr_ref != None:
		Print("      Destroy the SR.")
		StorageHandlerUtil.DestroySR(self.session, sr_ref)
		checkPoint += 1
	except Exception, e:
	    Print("- Could not cleanup the objects created during testing, please destroy the SR manually. Exception: %s" % str(e))
	    displayOperationStatus(False)
	    
        
	XenCertPrint("Checkpoints: %d, totalCheckPoints: %s" % (checkPoint, totalCheckPoints))
	return (retVal, checkPoint, totalCheckPoints)

    def MPConfigVerificationTests(self):
        try:
            sr_ref = None
            vdi_ref = None
            vbd_ref = None
	    retVal =True
	    checkPoint = 0
	    totalCheckPoints = 6
	    iterationCount = 100
	    
	    # Check if block unblock callouts have been defined. Else display an error and fail this test
	    if self.storage_conf['pathHandlerUtil'] == None: 		
		raise Exception("Path handler util not specified for multipathing tests.")
		
	    if not os.path.exists(self.storage_conf['pathHandlerUtil']): 		
		raise Exception("Path handler util specified for multipathing tests does not exist!")
	    
	    if self.storage_conf['storage_type'] == 'lvmohba' and self.storage_conf['pathInfo'] == None: 
		raise Exception("Path related information not specified for storage type lvmohba.")
	    
	    if self.storage_conf['count'] != None:
		iterationCount = int(self.storage_conf['count']) + 1
	    
	    #1. Enable host Multipathing
            disableMP = False
            if not StorageHandlerUtil.IsMPEnabled(self.session, util.get_localhost_uuid(self.session)): 
                StorageHandlerUtil.enable_multipathing(self.session, util.get_localhost_uuid(self.session))
                disableMP = True

   	    #2. Create and plug SR
	    Print("CREATING SR")
 	    (retVal, sr_ref, device_config) = self.Create()	    
	    if not retVal:		    
		raise Exception("      SR creation failed.")
	    else:
		displayOperationStatus(True)
		checkPoint += 1

	    Print("MULTIPATH AUTOMATED PATH FAILOVER TESTING")

	    if not self.GetPathStatus(device_config):
		Print("   - Failed to get and display path status.")
	    else:
		checkPoint += 1

            Print(">> Starting Random Path Block and Restore Iteration test")
	    Print("   This test will choose a random selection of upto (n -1) paths ")
	    Print("   of a total of n to block, and verify that the IO continues")
	    Print("   i.e. the correct paths are detected as failed, within 50 seconds.")
	    Print("   The test then verifies that after unblocking the path, it is ")
	    Print("   restored within 2 minutes.\n\n")
	    Print("   Path Connectivity Details")
	    self.DisplayPathStatus()

	    # make sure there are at least 2 paths for the multipath tests to make any sense.
	    if len(self.listPathConfig) < 2:
		raise Exception("FATAL! At least 2 paths are required for multipath failover testing, please configure your storage accordingly.")
	    
	    # Now testing failure times for the paths.  
            (retVal, vdi_ref, vbd_ref, vdi_size) = StorageHandlerUtil.CreateMaxSizeVDIAndVBD(self.session, sr_ref)
	    if not retVal:
		raise Exception("Failed to create max size VDI and VBD.")
	    else:
	        checkPoint += 2
	   
	    global retValIO
   	    global timeTaken
	    global bytesCopied
	    global speedOfCopy
	    Print("")
	    Print("Iteration 1:\n")
	    Print(" -> No manual blocking of paths.")
            s = TimedDeviceIO(self.session.xenapi.VBD.get_device(vbd_ref))
            s.start()
            s.join()
	    
	    if retValIO != 0:
		displayOperationStatus(False)
		raise Exception(" IO tests failed for device: %s" % self.session.xenapi.VBD.get_device(vbd_ref))
	    
            initialDataCopyTime = float(timeTaken.split()[0])
	    if initialDataCopyTime > 3:
		displayOperationStatus(False, timeTaken)
		Print("    - The initial data copy is too slow at %s" % timeTaken )
		dataCopyTooSlow = True
	    else:
		Print("    - IO test passed. Time: %s. Data: %s. Throughput: %s" % (timeTaken, '1MB', speedOfCopy))
		displayOperationStatus(True)
	        checkPoint += 1

	    if len(self.listPathConfig) > 1:		
		for i in range(2, iterationCount):
		    maxTimeTaken = 0
		    throughputForMaxTime = ''
		    totalCheckPoints += 2
		    Print("Iteration %d:\n" % i)				    
		    if not self.RandomlyFailPaths():		    			
			raise Exception("Failed to block paths.")
		    
		    s = WaitForFailover(self.session, device_config['SCSIid'], len(self.listPathConfig), self.noOfPaths)
		    s.start()
		    
		    while s.isAlive():
			timeTaken = 0
			s1 = TimedDeviceIO(self.session.xenapi.VBD.get_device(vbd_ref))		    
			s1.start()
			s1.join()
			
			if retValIO != 0:			
			    displayOperationStatus(False)
			    raise Exception("    - IO test failed for device %s." % self.session.xenapi.VBD.get_device(vbd_ref))
			else:
			    XenCertPrint("    - IO test passed. Time: %s. Data: %s. Throughput: %s." % (timeTaken, '1MB', speedOfCopy))
			    
			if timeTaken > maxTimeTaken:
			    maxTimeTaken = timeTaken
			    throughputForMaxTime = speedOfCopy
		    
		    if pathsFailed:
			Print("    - Paths failover time: %s seconds" % failoverTime)
			Print("    - Maximum IO completion time: %s. Data: %s. Throughput: %s" % (maxTimeTaken, '1MB', throughputForMaxTime))
			displayOperationStatus(True)
			checkPoint += 1
		    else:
			displayOperationStatus(False)
			self.BlockUnblockPaths(False, self.storage_conf['pathHandlerUtil'], self.noOfPaths, self.blockedpathinfo)
			raise Exception("    - Paths did not failover within expected time.")
		    
		    self.BlockUnblockPaths(False, self.storage_conf['pathHandlerUtil'], self.noOfPaths, self.blockedpathinfo)
		    Print(" -> Unblocking paths, waiting for restoration.")
		    count = 0
		    pathsMatch = False
		    while not pathsMatch and count < 120:
			pathsMatch = self.DoNewPathsMatch(device_config)
			time.sleep(1)
			count += 1
			
		    if not pathsMatch:
			displayOperationStatus(False, "> 2 mins")
			retVal = False 
			raise Exception("The path restoration took more than 2 mins.")
		    else:
			displayOperationStatus(True, " " + str(count) + " seconds")
			checkPoint += 1

            Print("- Test succeeded.")

	    # If multipath was enabled by us, disable it, else continue.
	    if disableMP:
	        StorageHandlerUtil.disable_multipathing(self.session, util.get_localhost_uuid(self.session))
 
	except Exception, e:
 	    Print("- There was an exception while performing multipathing configuration tests.")
	    Print("  Exception: %s" % str(e))
	    displayOperationStatus(False)
	    retVal = False

        try:
	    # Try cleaning up here
	    if vbd_ref != None:
		self.session.xenapi.VBD.unplug(vbd_ref)
		XenCertPrint("Unplugged VBD %s" % vbd_ref)
		self.session.xenapi.VBD.destroy(vbd_ref)
		XenCertPrint("Destroyed VBD %s" % vbd_ref)

	    if vdi_ref != None:
		self.session.xenapi.VDI.destroy(vdi_ref)
		XenCertPrint("Destroyed VDI %s" % vdi_ref)

	    # Try cleaning up here
            if sr_ref != None:
		Print("      Destroy the SR.")
		StorageHandlerUtil.DestroySR(self.session, sr_ref)
		
	    checkPoint += 1
		
	except Exception, e:
	    Print("- Could not cleanup the objects created during testing, VBD: %s VDI:%s SR:%s. Please destroy the objects manually. Exception: %s" % (vbd_ref, vdi_ref, sr_ref, str(e)))
	    displayOperationStatus(False)

	XenCertPrint("Checkpoints: %d, totalCheckPoints: %s" % (checkPoint, totalCheckPoints))
        return (retVal, checkPoint, totalCheckPoints)
	
    def DataPerformanceTests(self):
        try:
            sr_ref = None
            vdi_ref1 = None
            vdi_ref2 = None
            vbd_ref1 = None
            vbd_ref2 = None

            #1. Create and plug SR
            XenCertPrint("First use XAPI to get information for creating an SR.")
            mapIqnToListPortal = {}
            mapIqnToListSCSIId = {}
            (retVal, mapIqnToListPortal, mapIqnToListSCSIId) = self.GetIqnPortalScsiIdMap(self.storage_conf['target'], self.storage_conf['chapuser'], self.storage_conf['chappasswd'])
            
	    iqnToUse = None
            scsiIdToUse = None
            device_config = {}
            device_config['target'] = self.storage_conf['target']
	    if self.storage_conf['chapuser']!= None and self.storage_conf['chappasswd'] != None:
      	        device_config['chapuser'] = self.storage_conf['chapuser']
   	        device_config['chappassword'] = self.storage_conf['chappasswd']
            
            XenCertPrint("First use XAPI to get information for creating an SR.")
            for iqn in mapIqnToListSCSIId.keys():
                for scsiId in mapIqnToListSCSIId[iqn]:
                    try:
			device_config['targetIQN'] = iqn
                        device_config['SCSIid'] = scsiId
			sr_ref = self.session.xenapi.SR.create(util.get_localhost_uuid(self.session), device_config, '0', 'XenCertTestSR', '', 'lvmoiscsi', '',False, {})
			XenCertPrint("Created the SR %s using device_config: %s" % (sr_ref, device_config))
			scsiIdToUse = scsiId
			break
		    except Exception, e:
			XenCertPrint("SR creation failed with iqn: %s, and SCSI id: %s, trying the next lun." %(iqn, scsiId))
		if scsiIdToUse == None:
		    XenCertPrint("Could not create an SR with any LUNs for IQN %s, trying with other IQNs." % iqn)
                else:
		    XenCertPrint("Created the SR with IQN %s, and SCSIid %s so exiting the loop." % (iqn, scsiIdToUse))
		    break
	    if scsiIdToUse == None:
		XenCertPrint("Could not create an SR with any IQNs." % iqn)		
		raise Exception("Could not create any SRs with the IQN %s." % iqn)

	    # Now create 2 VDIs of 10GiB each
            # Populate VDI args
            args={}
            args['name_label'] = 'XenCertTestVDI1'
            args['SR'] = sr_ref
            args['name_description'] = ''
            args['virtual_size'] = '1073741824'
            args['type'] = 'user'
            args['sharable'] = False
            args['read_only'] = False
            args['other_config'] = {}
            args['sm_config'] = {}
            args['xenstore_data'] = {}
            args['tags'] = []
            XenCertPrint("The VDI create parameters are %s" % args)
            vdi_ref1 = self.session.xenapi.VDI.create(args)
            XenCertPrint("Created new VDI %s" % vdi_ref1)
            Print(" - Create a VDI on this SR, of size 1GiB.")

            # Populate VDI args
            args={}
            args['name_label'] = 'XenCertTestVDI2'
            args['SR'] = sr_ref
            args['name_description'] = ''
            args['virtual_size'] = '1073741824'
            args['type'] = 'user'
            args['sharable'] = False
            args['read_only'] = False
            args['other_config'] = {}
            args['sm_config'] = {}
            args['xenstore_data'] = {}
            args['tags'] = []
            XenCertPrint("The VDI create parameters are %s" % args)
            vdi_ref2 = self.session.xenapi.VDI.create(args)
            XenCertPrint("Created new VDI %s" % vdi_ref2)
            Print(" - Create another VDI on this SR, of size 1GiB.")

        except Exception, e:
            Print("There was an exception while performing multipathing configuration tests.")

        try:
            # Try cleaning up here
            if vbd_ref1 != None:
                self.session.xenapi.VBD.unplug(vbd1_ref)
                XenCertPrint("Unplugged VBD %s" % vbd1_ref)
                self.session.xenapi.VBD.destroy(vbd1_ref)
                XenCertPrint("Destroyed VBD %s" % vbd1_ref)

            if vbd_ref2 != None:
                self.session.xenapi.VBD.unplug(vbd2_ref)
                XenCertPrint("Unplugged VBD %s" % vbd2_ref)
                self.session.xenapi.VBD.destroy(vbd2_ref)
                XenCertPrint("Destroyed VBD %s" % vbd2_ref)

            XenCertPrint("Destroying VDI %s" % vdi_ref1)
            if vdi_ref1 != None:
                self.session.xenapi.VDI.destroy(vdi_ref1)
                XenCertPrint("Destroyed VDI %s" % vdi_ref1)

            XenCertPrint("Destroying VDI %s" % vdi_ref2)
            if vdi_ref2 != None:
                self.session.xenapi.VDI.destroy(vdi_ref2)
                XenCertPrint("Destroyed VDI %s" % vdi_ref2)

            if sr_ref != None:
                # First get the PBDs
                pbds = self.session.xenapi.SR.get_PBDs(sr_ref)
                XenCertPrint("Got the list of pbds for the sr %s as %s" % (sr_ref, pbds))
                XenCertPrint(" - Now unplug and destroy PBDs for the SR.")
                for pbd in pbds:
                    XenCertPrint("Looking at PBD: %s" % pbd)
                    self.session.xenapi.PBD.unplug(pbd)
                    self.session.xenapi.PBD.destroy(pbd)
                XenCertPrint(" - Now forget the SR.")
                XenCertPrint(" - Now forget the SR: %s" % sr_ref)
                self.session.xenapi.SR.forget(sr_ref)
        except Exception, e:
            Print("Could not cleanup the objects created during testing, please destroy the SR manually.")
	XenCertPrint("Checkpoints: %d, totalCheckPoints: %s" % (checkPoint, totalCheckPoints))        
    
    def PoolTests(self):
        try:
            sr_ref = None
	    retVal = True
	    checkPoint = 0
	    totalCheckPoints = 4

	    #1. Enable host Multipathing
	    Print("POOL CONSISTENCY TESTS.")
	    Print(">> This test creates shared SRs and verifies that PBD records ")
	    Print("   display the same number of paths for each host in the pool.")
	    Print("   -> Enabling multipathing on each host in the pool.")
	    host_disable_MP_list = []
	    host_list = self.session.xenapi.host.get_all()
	    for host in host_list:
		if not StorageHandlerUtil.IsMPEnabled(self.session, host):
                    StorageHandlerUtil.enable_multipathing(self.session, host)
                    host_disable_MP_list.append(host)

            displayOperationStatus(True)
	    checkPoint += 1

   	    #Create and plug SR
	    XenCertPrint( "2. Now use XAPI to get information for creating an SR."           )
	    Print("   -> Creating shared SR.")
 	    (retVal, sr_ref, device_config) = self.Create()
	    if not retVal:		    
		raise Exception("      SR creation failed.")
	    else:
		checkPoint += 1
		    
	    # Now check PBDs for this SR and make sure all PBDs reflect the same number of active and passive paths for hosts with multipathing enabled.  
	    Print("   -> Checking paths reflected on PBDs for each host.")
            my_pbd = util.find_my_pbd(self.session, util.get_localhost_uuid(self.session), sr_ref)
            ref_other_config = self.session.xenapi.PBD.get_other_config(my_pbd)
	    Print("      %-50s %-10s" % ('Host', '[Active, Passive]'))
            for key in ref_other_config.keys():
        	if key.find(device_config['SCSIid']) != -1:
		    Print("      %-50s %-10s" % (util.get_localhost_uuid(self.session), ref_other_config[key]))
                    break
       
	    pbds = self.session.xenapi.SR.get_PBDs(sr_ref)
            for pbd in pbds: 
	        if pbd == my_pbd:
		    continue
	        else:
            	    host_ref = self.session.xenapi.PBD.get_host(pbd)
                    if StorageHandlerUtil.IsMPEnabled(self.session, host_ref):
		        other_config = self.session.xenapi.PBD.get_other_config(pbd)
                        for key in other_config.keys():
			    if key.find(device_config['SCSIid']):
		                Print("      %-50s %-10s" % (util.get_localhost_uuid(self.session), ref_other_config[key]))
		            break	
	    displayOperationStatus(True)
	    checkPoint += 1
 
	except Exception, e:
            Print("      There was an exception while performing pool consistency tests. Exception: %s. Please check the log for details." % str(e))
	    displayOperationStatus(False)
	    retVal = False

        try:
	    # Try cleaning up here
	    if sr_ref != None:
		Print("      Destroy the SR.")
		StorageHandlerUtil.DestroySR(self.session, sr_ref)		
	    checkPoint += 1

	except Exception, e:
	    Print("Could not cleanup the SR created during testing, SR: %s. Exception: %s. Please destroy the SR manually." % (sr_ref, str(e)))
	    displayOperationStatus(False)

        Print("   -> Disable multipathing on hosts. ")
	for host in host_disable_MP_list:
	    StorageHandlerUtil.disable_multipathing(self.session, host)
	
	XenCertPrint("Checkpoints: %d, totalCheckPoints: %s" % (checkPoint, totalCheckPoints))
        return (retVal, checkPoint, totalCheckPoints)
    
    # blockOrUnblock = True for block, False for unblock
    def BlockUnblockPaths(self, blockOrUnblock, script, noOfPaths, passthrough):
	try:
	    stdout = ''
	    if blockOrUnblock:
		cmd = [os.path.join(os.getcwd(), 'blockunblockpaths'), script, 'block', str(noOfPaths), passthrough]
	    else:
		cmd = [os.path.join(os.getcwd(), 'blockunblockpaths'), script, 'unblock', str(noOfPaths), passthrough]
	    
	    (rc, stdout, stderr) = util.doexec(cmd,'')

	    XenCertPrint("The path block/unblock utility returned rc: %s stdout: %s, stderr: %s" % (rc, stdout, stderr))
 	    if rc != 0:		
	    	raise Exception("   - The path block/unblock utility returned an error: %s. Please block/unblock the paths %s manually." % (stderr, passthrough))
	    return stdout
	except Exception, e:	    
	    raise e	
    
    def __del__(self):
        XenCertPrint("Reached Storagehandler destructor")
        self.session.xenapi.session.logout() 
	
    def Create(self):
	# This class specific function will create an SR of the required type and return the required parameters.
	XenCertPrint("Reached StorageHandler Create")
	
    def DoNewPathsMatch(self, device_config):
	try:
	    # get new config
	    (retVal, listPathConfigNew) = StorageHandlerUtil.get_path_status(device_config['SCSIid'])
	    XenCertPrint("listpathconfig: %s" % self.listPathConfig)
	    XenCertPrint("listpathconfigNew: %s" % listPathConfigNew)
	    if not retVal:	        
		raise Exception("     - Failed to get path status information for SCSI Id: %s" % device_config['SCSIid'])
	    for i in range(0, len(self.listPathConfig)):
		if listPathConfigNew[i][2] != self.listPathConfig[i][2]:		            
		    return False
	    return True
	except:
	    XenCertPrint("Failed to match new paths with old paths.")
	    return False	
	
class StorageHandlerISCSI(StorageHandler):
    def __init__(self, storage_conf):
        XenCertPrint("Reached StorageHandlerISCSI constructor")
	self.iqn = storage_conf['targetIQN']
        StorageHandler.__init__(self, storage_conf)
	
    def Create(self):
	device_config = {}
	retVal = True
	sr_ref = None
	try:
            XenCertPrint("First use XAPI to get information for creating an SR.")
 	    listPortal = []
	    listSCSIId = []
            (listPortal, listSCSIId) = StorageHandlerUtil.GetListPortalScsiIdForIqn(self.session, self.storage_conf['target'], self.iqn, self.storage_conf['chapuser'], self.storage_conf['chappasswd'])
	    
	    # Create an SR
	    Print("      Creating the SR.")
	    device_config['target'] = self.storage_conf['target']
	    if len(self.iqn.split(',')) > 1:
		device_config['targetIQN'] = '*'
	    else:
		device_config['targetIQN'] = self.iqn
	    if self.storage_conf['chapuser']!= None and self.storage_conf['chappasswd'] != None:
		device_config['chapuser'] = self.storage_conf['chapuser']
		device_config['chappassword'] = self.storage_conf['chappasswd']
	    # try to create an SR with one of the LUNs mapped, if all fails throw an exception
	    for scsiId in listSCSIId:
		try:		    
                    device_config['SCSIid'] = scsiId
		    XenCertPrint("The SR create parameters are %s, %s" % (util.get_localhost_uuid(self.session), device_config))
		    sr_ref = self.session.xenapi.SR.create(util.get_localhost_uuid(self.session), device_config, '0', 'XenCertTestSR', '', 'lvmoiscsi', '',False, {})
		    XenCertPrint("Created the SR %s using device_config %s" % (sr_ref, device_config))
		    displayOperationStatus(True)
                    break

		except:
		    XenCertPrint("Could not perform SR control tests with device %s, trying other devices." % scsiId)
		    continue
		    
	    if sr_ref == None:
		displayOperationStatus(False)
		retVal = False
	except Exception, e:
	    Print("   - Failed to create SR. Exception: %s" % str(e))
	    displayOperationStatus(False)
	    raise Exception(str(e))
	
	return (retVal, sr_ref, device_config)
	
    def GetPathStatus(self, device_config):
	# Query DM-multipath status, reporting a) Path checker b) Path Priority handler c) Number of paths d) distribution of active vs passive paths
	try:
            self.mapIPToHost = StorageHandlerUtil._init_adapters()      
            XenCertPrint("The IP to host id map is: %s" % self.mapIPToHost) 
            
	    (retVal, configMap) = StorageHandlerUtil.GetConfig(device_config['SCSIid'])
	    if not retVal:		
		raise Exception("   - Failed to get SCSI config information for SCSI Id: %s" % device_config['SCSIid'])

            XenCertPrint("The config map extracted from scsi_id %s is %s" % (device_config['SCSIid'], configMap))
            
            # Get path_checker and priority handler for this device.
            (retVal, mpath_config) = StorageHandlerUtil.parse_config(configMap['ID_VENDOR'], configMap['ID_MODEL'])
	    if not retVal:		
		raise Exception("   - Failed to get multipathd config information for vendor: %s and product: %s" % (configMap['ID_VENDOR'], configMap['ID_MODEL']))
            XenCertPrint("The mpath config extracted from multipathd is %s" % mpath_config)

	    Print(">> Multipathd enabled for %s, %s with the following config" % (configMap['ID_VENDOR'], configMap['ID_MODEL']))
	    Print("   please confirm that these settings are optimal:")
	    Print("     device {")
	    for key in mpath_config:
		Print("             %s %s" % (key, mpath_config[key]))

	    Print("     }")
 
	    (retVal, self.listPathConfig) = StorageHandlerUtil.get_path_status(device_config['SCSIid'])
	    if not retVal:		
		raise Exception("Failed to get path status information for SCSI Id: %s" % device_config['SCSIid'])
            XenCertPrint("The path status extracted from multipathd is %s" % self.listPathConfig)
	    
	    return True
	except Exception, e:
	    Print("   - Failed to get path status for device_config: %s. Exception: %s" % (device_config, str(e)))
	    return False	    

    def DisplayPathStatus(self):
	Print("       %-15s %-15s %-25s %-15s" % ('IP address', 'HBTL','Path DM status','Path status')            )
        for item in self.listPathConfig:
	    Print("       %-15s %-15s %-25s %-15s" % (StorageHandlerUtil.findIPAddress(self.mapIPToHost, item[0]), item[0], item[1], item[2]))
	    
    def RandomlyFailPaths(self):
	try:
	    self.noOfPaths = random.randint(1, len(self.listPathConfig) -1 )   
            self.blockedpathinfo = ''
	    self.paths = ''
	    for item in self.listPathConfig: 
		ip = StorageHandlerUtil.findIPAddress(self.mapIPToHost, item[0])
		self.paths += ip + ','
               	
	    self.paths = self.paths.rstrip(',')
	    (self.blockedpathinfo) = self.BlockUnblockPaths(True, self.storage_conf['pathHandlerUtil'], self.noOfPaths, self.paths)
	    PrintOnSameLine(" -> Blocking %d paths (%s)\n" % (self.noOfPaths, self.blockedpathinfo))
	    return True		    
	except Exception, e:
	    raise e
	
    def FunctionalTests(self):
        logoutlist = []
	retVal = True
	checkPoint = 0
	totalCheckPoints = 4
	timeForIOTestsInSec = 0
	totalSizeInMiB = 0
	wildcard = False

        try:
            # Take SR device-config parameters and initialise data path layer.        
	    Print("INITIALIZING SCSI DATA PATH LAYER ")
	    
	    iqns = self.storage_conf['targetIQN'].split(',')
	    if len(iqns) == 1 and iqns[0]=='*':
		wildcard = True
	    listPortalIQNs = []
	    for target in self.storage_conf['target'].split(','):
		try:
		    map = iscsilib.discovery(target, ISCSISR.DEFAULT_PORT, self.storage_conf['chapuser'], self.storage_conf['chappasswd'])		    		    
		except Exception, e:
		    Print("Exception discovering iscsi target: %s, exception: %s" % (target, str(e)))
		    displayOperationStatus(False)                
	    
		# Create a list of portal IQN combinations.		
		for record in map:
		    for iqn in iqns:
			if record[2] == iqn or wildcard:
			    try:
				listPortalIQNs.index((record[0], record[2]))
			    except:    
				listPortalIQNs.append((record[0], record[2]))
				break
	    
	    displayOperationStatus(True)
	    checkPoint += 1

            # Now traverse through this multimap and for each IQN
            # Connect to all available portals in turn and verify that
            Print("DISCOVERING ADVERTISED SESSION TARGETS")
            Print("   %-70s %-20s" % ('IQN', 'Session Target'))
            for (portal, iqn) in listPortalIQNs:
		Print("   %-70s %-20s" % (iqn, portal))
	
	    displayOperationStatus(True)
	    checkPoint += 1

            Print("REPORT LUNS EXPOSED")
	    Print(">> This test logs on to all the advertised target and IQN combinations")
	    Print("   and discovers the LUNs exposed by each including information")
	    Print("   like the LUN ID, SCSI ID and the size of each LUN.")
	    Print("   This test also verifies that all the sessions from the same IQN ")
	    Print("   expose the same number of LUNs and the same LUNs.")
	    Print("")
	    # Create a map of the following format
	    # SCSIid -> (portal, iqn, device) tuple list	    
	    scsiToTupleMap = {}
	    # and one of the following format
	    # iqn -> [SCSI IDS]
	    # for each portal below, check if iqn is in the map
	    # if yes check if the SCSI Ids match, else report error
	    # if iqn not in map add iqn and list of SCSI IDs.
	    iqnToScsiList = {}
	    firstPortal = True
            for (portal, iqn) in listPortalIQNs:
		try:
		    scsilist = []
		    # Login to this IQN, portal combination
		    iscsilib.login(portal, iqn, self.storage_conf['chapuser'], self.storage_conf['chappasswd'])
		    XenCertPrint("Logged on to the target.")
		    logoutlist.append((portal,iqn))                        
                            
		    # Now test the target
		    iscsilib._checkTGT(portal)
		    XenCertPrint("Checked the target.")
		    lunToScsi = StorageHandlerUtil.get_lun_scsiid_devicename_mapping(iqn, portal)
		    if len(lunToScsi.keys()) == 0:
			raise Exception("   - No LUNs found!")
                        
		    XenCertPrint("The portal %s and the iqn %s yielded the following LUNs on discovery:" % (portal, iqn))
		    mapDeviceToHBTL = scsiutil.cacheSCSIidentifiers()
		    XenCertPrint("The mapDeviceToHBTL is %s" % mapDeviceToHBTL)
			  
		    if firstPortal:
			Print("     %-23s\t%-4s\t%-34s\t%-10s" % ('PORTAL', 'LUN', 'SCSI-ID', 'Size(MiB)'))
			firstPortal = False
		    for key in lunToScsi.keys():
		        # Find the HBTL for this lun
			scsilist.append(lunToScsi[key][0])
		        HBTL = mapDeviceToHBTL[lunToScsi[key][1]]
		        HBTL_id = HBTL[1] + ":" + HBTL[2] + ":" + HBTL[3] + ":" + HBTL[4]
		        filepath = '/sys/class/scsi_device/' + HBTL_id + '/device/block:*/size'
		        XenCertPrint("The filepath is: %s" % filepath)
		        filelist = glob.glob(filepath)
		        XenCertPrint("The HBTL_id is %s. The filelist is: %s" % (HBTL_id, filelist))
		        sectors = util.get_single_entry(filelist[0])
		        size = int(sectors) * 512 / 1024 / 1024
			Print("     %-23s\t%-4s\t%-34s\t%-10s" % (portal, key, lunToScsi[key][0], size))
			timeForIOTestsInSec += StorageHandlerUtil.FindDiskDataTestEstimate(lunToScsi[key][1], size)
			if scsiToTupleMap.has_key(lunToScsi[key][0]):
			    scsiToTupleMap[lunToScsi[key][0]].append(( portal, iqn, lunToScsi[key][1]))
			else:
			    scsiToTupleMap[lunToScsi[key][0]] = [( portal, iqn, lunToScsi[key][1])]
			
			totalSizeInMiB += size			   		        
	        except Exception, e:
		    Print("     WARNING: No LUNs reported by portal %s for iqn %s. Exception: %s" % (portal, iqn, str(e)))
		    XenCertPrint("     WARNING: No LUNs reported by portal %s for iqn %s." % (portal, iqn))
		    continue
		
		if iqnToScsiList.has_key(iqn):
		    XenCertPrint("Reference scsilist: %s, current scsilist: %s" % (iqnToScsiList[iqn], scsilist))
		    if iqnToScsiList[iqn].sort() != scsilist.sort():
			raise Exception("     ERROR: LUNs reported by portal %s for iqn %s do not match LUNs reported by other portals of the same IQN." % (portal, iqn))
		else:
		    iqnToScsiList[iqn] = scsilist
			
	    displayOperationStatus(True)
	    checkPoint += 1

            Print("DISK IO TESTS")
	    Print(">> This tests execute a disk IO test against each available LUN to verify ")
	    Print("   that they are writeable and there is no apparent disk corruption.")
	    Print("   the tests attempt to write to the LUN over each available path and")
	    Print("   reports the number of writable paths to each LUN.")
	    seconds = timeForIOTestsInSec
	    minutes = 0
	    hrs = 0
	    XenCertPrint("Total estimated time for the disk IO tests in seconds: %d" % timeForIOTestsInSec)
	    if timeForIOTestsInSec > 60:
		minutes = timeForIOTestsInSec/60
		seconds = int(timeForIOTestsInSec - (minutes * 60))
		if minutes > 60:
		    hrs = int(minutes/60)
		    minutes = int(minutes - (hrs * 60))
	    
	    if hrs > timeLimitFunctional or hrs == timeLimitFunctional and minutes > 0:
		raise Exception("The disk IO tests will take more than %s hours, please restrict the total disk sizes above to %d GiB."
				% (timeLimitFunctional, (timeLimitFunctional*60*60*totalSizeInMiB)/timeForIOTestsInSec))
		
	    Print("   START TIME: %s " % (time.asctime(time.localtime())))
	    
	    if hrs > 0:
		Print("   APPROXIMATE RUN TIME: %s hours, %s minutes, %s seconds." % (hrs, minutes, seconds))
	    elif minutes > 0:
		Print("   APPROXIMATE RUN TIME: %s minutes, %s seconds." % (minutes, seconds))
	    elif seconds > 0:
		Print("   APPROXIMATE RUN TIME: %s seconds." % seconds)
	    
	    Print("")
	    firstPortal = True
	    lunsMatch = True
	    for key in scsiToTupleMap.keys():				
		try:		    
		    totalCheckPoints += 1
		    Print("     - Testing LUN with SCSI ID %-30s" % key)
		    
		    pathNo = 0
		    pathPassed = 0
		    for tuple in scsiToTupleMap[key]:
			pathNo += 1			
			# tuple = (portal, iqn, device)
			# If this is a root device then skip IO tests for this device.
			if os.path.realpath(util.getrootdev()) == tuple[2]:
			    Print("     -> Skipping IO tests on device %s, as it is the root device." % tuple[2])
	
			# Execute a disk IO test against each path to the LUN to verify that it is writeable
			# and there is no apparent disk corruption
			PrintOnSameLine("        Path num: %d. Device: %s" % (pathNo, tuple[2]))
			try:
			    # First write a small chunk on the device to make sure it works		    
			    XenCertPrint("First write a small chunk on the device %s to make sure it works." % tuple[2])
			    cmd = ['dd', 'if=/dev/zero', 'of=%s' % tuple[2], 'bs=1M', 'count=1', 'conv=nocreat', 'oflag=direct']
			    util.pread(cmd)
			    			    
			    cmd = ['./diskdatatest', 'write', '1', tuple[2]]
			    XenCertPrint("The command to be fired is: %s" % cmd)
			    util.pread(cmd)
			    
			    cmd = ['./diskdatatest', 'verify', '1', tuple[2]]
			    XenCertPrint("The command to be fired is: %s" % cmd)
			    util.pread(cmd)
			    
			    XenCertPrint("Device %s passed the disk IO test. " % tuple[2])
			    pathPassed += 1
			    Print("")
			    displayOperationStatus(True)
			    
			except Exception, e:  
			    Print("        Exception: %s" % str(e))
			    displayOperationStatus(False)
			    XenCertPrint("Device %s failed the disk IO test. Please check if the disk is writable." % tuple[2] )
			
		    if pathPassed == 0:
			displayOperationStatus(False)
			raise Exception("     - LUN with SCSI ID %-30s. Failed the IO test, none of the paths were writable." % key)			
		    else:
			Print("        SCSI ID: %s Total paths: %d. Writable paths: %d." % (key, len(scsiToTupleMap[key]), pathPassed))
			displayOperationStatus(True)
			checkPoint += 1			    
				
	        except Exception, e:                    
                    raise Exception("   - Testing failed while testing devices with SCSI ID: %s." % key)
		
	    Print("   END TIME: %s " % (time.asctime(time.localtime())))
	    
	    checkPoint += 1
        
        except Exception, e:
            Print("- Functional testing failed due to an exception.")
	    Print("- Exception: %s"  % str(e))
	    retVal = False
            
         # Logout of all the sessions in the logout list
        for (portal,iqn) in logoutlist:
            try:
                XenCertPrint("Logging out of the session: %s, %s" % (portal, iqn))
                iscsilib.logout(portal, iqn) 
            except Exception, e:
 		Print("- Logout failed for the combination %s, %s, but it may not have been logged on so ignore the failure." % (portal, iqn))
		Print("  Exception: %s" % str(e))
	XenCertPrint("Checkpoints: %d, totalCheckPoints: %s" % (checkPoint, totalCheckPoints))
        XenCertPrint("Leaving StorageHandlerISCSI FunctionalTests")

	return (retVal, checkPoint, totalCheckPoints)
    
    def __del__(self):
        XenCertPrint("Reached StorageHandlerISCSI destructor")
	StorageHandler.__del__(self)
        
class StorageHandlerHBA(StorageHandler):
    def __init__(self, storage_conf):
        XenCertPrint("Reached StorageHandlerHBA constructor")
	self.mapHBA = {}
        StorageHandler.__init__(self, storage_conf)

    def Create(self):
        device_config = {}
        retVal = True
	sr_ref = None
        try:
            XenCertPrint("First use XAPI to get information for creating an SR.")
            listSCSIId = []
            (retVal, listAdapters, listSCSIId) = StorageHandlerUtil.GetHBAInformation(self.session)
            if not retVal:		
                raise Exception("   - Failed to get available HBA information on the host.")
	    if len(listSCSIId) == 0:		
                raise Exception("   - Failed to get available LUNs on the host.")

            # Create an SR
            # try to create an SR with one of the LUNs mapped, if all fails throw an exception
	    Print("      Creating the SR.")
            for scsiId in listSCSIId:
                try:
                    device_config['SCSIid'] = scsiId
                    XenCertPrint("The SR create parameters are %s, %s" % (util.get_localhost_uuid(self.session), device_config))
                    sr_ref = self.session.xenapi.SR.create(util.get_localhost_uuid(self.session), device_config, '0', 'XenCertTestSR', '', 'lvmohba', '',False, {})
                    XenCertPrint("Created the SR %s using device_config %s" % (sr_ref, device_config))
                    displayOperationStatus(True)
                    break

                except:
                    XenCertPrint("Could not perform SR control tests with device %s, trying other devices." % scsiId)
                    continue

            if sr_ref == None:
                displayOperationStatus(False)
                retVal = False
        except Exception, e:
	    Print("   - Failed to create SR. Exception: %s" % str(e))
            displayOperationStatus(False)
            raise Exception(str(e))

        return (retVal, sr_ref, device_config)

    def GetPathStatus(self, device_config):
	# Query DM-multipath status, reporting a) Path checker b) Path Priority handler c) Number of paths d) distribution of active vs passive paths
	try:            
	    (retVal, configMap) = StorageHandlerUtil.GetConfig(device_config['SCSIid'])
	    if not retVal:		
		raise Exception("   - Failed to get SCSI config information for SCSI Id: %s" % device_config['SCSIid'])

            XenCertPrint("The config map extracted from scsi_id %s is %s" % (device_config['SCSIid'], configMap))
            
            # Get path_checker and priority handler for this device.
            (retVal, mpath_config) = StorageHandlerUtil.parse_config(configMap['ID_VENDOR'], configMap['ID_MODEL'])
	    if not retVal:
		raise Exception("   - Failed to get multipathd config information for vendor: %s and product: %s" % (configMap['ID_VENDOR'], configMap['ID_MODEL']))
		
            XenCertPrint("The mpath config extracted from multipathd is %s" % mpath_config)

	    Print(">> Multipathd enabled for %s, %s with the following config:" % (configMap['ID_VENDOR'], configMap['ID_MODEL']))
	    Print("     device {")
	    for key in mpath_config:
		Print("             %s %s" % (key, mpath_config[key]))

	    Print("     }")
 
	    (retVal, self.listPathConfig) = StorageHandlerUtil.get_path_status(device_config['SCSIid'])
	    if not retVal:		
		raise Exception("Failed to get path status information for SCSI Id: %s" % device_config['SCSIid'])
            XenCertPrint("The path status extracted from multipathd is %s" % self.listPathConfig)
	    
	    return True
	except Exception, e:
	    Print("   - Failed to get path status for device_config: %s. Exception: %s" % (device_config, str(e)))
	    return False	    

    def DisplayPathStatus(self):
	Print("       %-15s %-25s %-15s" % ('HBTL','Path DM status','Path status')            )
        for item in self.listPathConfig:
	    Print("       %-15s %-25s %-15s" % (item[0], item[1], item[2]))
	    
    def RandomlyFailPaths(self):
	try:
	    self.blockedpathinfo = ''
	    self.noOfPaths = 0
	    scriptReturn = self.BlockUnblockPaths(True, self.storage_conf['pathHandlerUtil'], self.noOfPaths, self.storage_conf['pathInfo'])
	    self.noOfPaths = int(scriptReturn.split('::')[1])
	    XenCertPrint("No of paths which should fail: %s" % self.noOfPaths)
	    self.blockedpathinfo = scriptReturn.split('::')[0]
	    PrintOnSameLine(" -> Blocking paths (%s)\n" % self.blockedpathinfo)
	    return True
	except Exception, e:	    
	    raise e
	
    def FunctionalTests(self):
	retVal = True
	checkPoint = 0
	totalCheckPoints = 4
	timeForIOTestsInSec = 0
	totalSizeInMiB = 0
	
	try:
	    # Generate a map of the HBAs that the user want to test against.
	    if self.storage_conf['adapters'] != None:		
		for hba in self.storage_conf['adapters'].split(','):
		    self.mapHBA[hba] = 1

    	    # 1. Report the FC Host Adapters detected and the status of each physical port
	    # Run a probe on the host with type lvmohba, parse the xml output and extract the HBAs advertised
	    Print("DISCOVERING AVAILABLE HARDWARE HBAS")
	    (retVal, listMaps, scsilist) = StorageHandlerUtil.GetHBAInformation(self.session)
	    if not retVal:		
		raise Exception("   - Failed to get available HBA information on the host.")
	    else:
		XenCertPrint("Got HBA information: %s and SCSI ID list: %s" % (listMaps, scsilist))
           
	    if len(listMaps) == 0:	    	
 	    	raise Exception("   - No hardware HBAs found!")
 
	    checkPoint += 1
	    first = True
	    prunedList = []
	    for map in listMaps:		
		# If the user has selected particular HBAs then remove the others from the list.
		if len(self.mapHBA) != 0:
		    if self.mapHBA.has_key(map['name']):
		        prunedList.append(map)		    
		else:
		    prunedList.append(map)		    
		    
		if first:
		    for key in map.keys():
		        PrintOnSameLine("%-15s\t" % key)
		    PrintOnSameLine("\n")
	            first = False
		    
		for key in map.keys(): 
		    PrintOnSameLine("%-15s\t" % map[key])
		PrintOnSameLine("\n")
	    
	    if len(prunedList) == 0:
		displayOperationStatus(False)
		raise Exception("   - No available HBAs selected for the functional tests. Please provide a comma separated list of one or more HBAs mentioned above.")
	    else:
		displayOperationStatus(True)
		checkPoint += 1 
		
            # 2. Report the number of LUNs and the disk geometry for verification by user
	    # take each host id and look into /dev/disk/by-scsibus/*-<host-id>*
            # extract the SCSI ID from each such entries, make sure all have same
            # number of entries and the SCSI IDs are the same.
            # display SCSI IDs and luns for device for each host id. 
            Print("REPORT LUNS EXPOSED PER HOST")
            Print(">> This test discovers the LUNs exposed by each host id including information")
            Print("   like the HBTL, SCSI ID and the size of each LUN.")
            Print("   The test also ensures that all host ids ")
            Print("   expose the same number of LUNs and the same LUNs.")
            Print("")
            first = True
            hostIdToLunList = {}
	    # map from SCSI id -> list of devices
	    scsiToTupleMap = {}
	    for map in prunedList:
                try:
		    (retVal, listLunInfo) = StorageHandlerUtil.GetLunInformation(map['id'])
		    if not retVal:					    	
			raise Exception("Failed to get LUN information for host id: %s" % map['id'])
		    else:
		    	XenCertPrint("Got LUN information for host id %s as %s" % (map['id'], listLunInfo))
			hostIdToLunList[map['id']] = listLunInfo

                    Print("     The luns discovered for host id %s: " % map['id'])
                    mapDeviceToHBTL = scsiutil.cacheSCSIidentifiers()
                    XenCertPrint("The mapDeviceToHBTL is %s" % mapDeviceToHBTL)

                    if first and len(listLunInfo) > 0:
			Print("     %-4s\t%-34s\t%-20s\t%-10s" % ('LUN', 'SCSI-ID', 'Device', 'Size(MiB)'))
			first = False
			refListLuns = listLunInfo
		    else:
			# Compare with ref list to make sure the same LUNs have been exposed.
                        if len(listLunInfo) != len(refListLuns):			    
			    raise Exception("     - Different number of LUNs exposed by different host ids.")
                               
			# Now compare each element of the list to make sure it matches the ref list
			for lun in listLunInfo:
			    found = False
			    for refLun in refListLuns:
				if refLun['id'] == lun['id'] and refLun['SCSIid'] == lun['SCSIid']:
				    found = True
				    break
			    if not found:
				raise Exception("     - Different number of LUNs exposed by different host ids.")
			    else:
				continue
			checkPoint += 1
			    			
                    for lun in listLunInfo:
                        # Find the HBTL for this lun
                        HBTL = mapDeviceToHBTL[lun['device']]
                        HBTL_id = HBTL[1] + ":" + HBTL[2] + ":" + HBTL[3] + ":" + HBTL[4]
                        filepath = '/sys/class/scsi_device/' + HBTL_id + '/device/block:*/size'
                        XenCertPrint("The filepath is: %s" % filepath)
                        filelist = glob.glob(filepath)
                        XenCertPrint("The HBTL_id is %s. The filelist is: %s" % (HBTL_id, filelist))
                        sectors = util.get_single_entry(filelist[0])
                        size = int(sectors) * 512 / 1024 / 1024
                        Print("     %-4s\t%-34s\t%-20s\t%-10s" % (lun['id'], lun['SCSIid'], lun['device'], size))
			timeForIOTestsInSec += StorageHandlerUtil.FindDiskDataTestEstimate( lun['device'], size)
			if scsiToTupleMap.has_key(lun['SCSIid']):
			    scsiToTupleMap[lun['SCSIid']].append(lun['device'])
			else:
			    scsiToTupleMap[lun['SCSIid']] = [lun['device']]
			
			totalSizeInMiB += size	   

                except Exception, e:
                    Print("     EXCEPTION: No LUNs reported for host id %s." % map['id'])
                    continue
                displayOperationStatus(True)

            checkPoint += 1

	    # 3. Execute a disk IO test against each LUN to verify that they are writeable and there is no apparent disk corruption	    
            Print("DISK IO TESTS")
            Print(">> This tests execute a disk IO test against each available LUN to verify ")
            Print("   that they are writeable and there is no apparent disk corruption.")
            Print("   the tests attempt to write to the LUN over each available path and")
            Print("   reports the number of writable paths to each LUN.")
	    seconds = timeForIOTestsInSec
	    minutes = 0
	    hrs = 0
	    XenCertPrint("Total estimated time for the disk IO tests in seconds: %d" % timeForIOTestsInSec)
	    if timeForIOTestsInSec > 60:
		minutes = int(timeForIOTestsInSec/60)
		seconds = int(timeForIOTestsInSec - (minutes * 60))
		if minutes > 60:
		    hrs = int(minutes/60)
		    minutes = int(minutes - (hrs * 60))
	    
	    if hrs > timeLimitFunctional or hrs == timeLimitFunctional and minutes > 0:
		raise Exception("The disk IO tests will take more than %s hours, please restrict the total disk sizes above to %d GiB."
				% (timeLimitFunctional, (timeLimitFunctional*60*60*totalSizeInMiB)/timeForIOTestsInSec))		
		
	    Print("   START TIME: %s " % (time.asctime(time.localtime())))
	    if hrs > 0:
		Print("   APPROXIMATE RUN TIME: %s hours, %s minutes, %s seconds." % (hrs, minutes, seconds))
	    elif minutes > 0:
		Print("   APPROXIMATE RUN TIME: %s minutes, %s seconds." % (minutes, seconds))
	    elif seconds > 0:
		Print("   APPROXIMATE RUN TIME: %s seconds." % seconds)	    
	    
	    Print("")	    
            totalCheckPoints += 1
	    for key in scsiToTupleMap.keys():
                try:
                    totalCheckPoints += 1
                    Print("     - Testing LUN with SCSI ID %-30s" % key)

                    pathNo = 0
                    pathPassed = 0
                    for device in scsiToTupleMap[key]:
                        pathNo += 1
                        # tuple = (hostid, device)
                        # If this is a root device then skip IO tests for this device.
                        if os.path.realpath(util.getrootdev()) == device:
                            Print("     -> Skipping IO tests on device %s, as it is the root device." % device)

                        # Execute a disk IO test against each path to the LUN to verify that it is writeable
                        # and there is no apparent disk corruption
                        PrintOnSameLine("        Path num: %d. Device: %s" % (pathNo, device))
                        try:
                            # First write a small chunk on the device to make sure it works
                            XenCertPrint("First write a small chunk on the device %s to make sure it works." % device)
                            cmd = ['dd', 'if=/dev/zero', 'of=%s' % device, 'bs=1M', 'count=1', 'conv=nocreat', 'oflag=direct']
                            util.pread(cmd)

                            cmd = ['./diskdatatest', 'write', '1', device]
                            XenCertPrint("The command to be fired is: %s" % cmd)
                            util.pread(cmd)
                            
			    cmd = ['./diskdatatest', 'verify', '1', device]
                            XenCertPrint("The command to be fired is: %s" % cmd)
                            util.pread(cmd)
                            
                            XenCertPrint("Device %s passed the disk IO test. " % device)
                            pathPassed += 1
                            Print("")
                            displayOperationStatus(True)

                        except Exception, e:
                            Print("        Exception: %s" % str(e))
                            displayOperationStatus(False)
                            XenCertPrint("Device %s failed the disk IO test. Please check if the disk is writable." % device )
                    if pathPassed == 0:
			displayOperationStatus(False)
                        raise Exception("     - LUN with SCSI ID %-30s. Failed the IO test, none of the paths were writable." % key)                        
                    else:
                        Print("        SCSI ID: %s Total paths: %d. Writable paths: %d." % (key, len(scsiToTupleMap[key]), pathPassed))
                        displayOperationStatus(True)
                        checkPoint += 1

                except Exception, e:
                    raise Exception("   - Testing failed while testing devices with SCSI ID: %s." % key)

            Print("   END TIME: %s " % (time.asctime(time.localtime())))
            checkPoint += 1

        except Exception, e:
            Print("- Functional testing failed due to an exception.")
	    Print("- Exception: %s"  % str(e))
	    retVal = False
            
	XenCertPrint("Checkpoints: %d, totalCheckPoints: %s" % (checkPoint, totalCheckPoints))
        XenCertPrint("Leaving StorageHandlerHBA FunctionalTests")

	return (retVal, checkPoint, totalCheckPoints)
    
    def __del__(self):
        XenCertPrint("Reached StorageHandlerHBA destructor")
	StorageHandler.__del__(self)

class StorageHandlerNFS(StorageHandler):
    def __init__(self, storage_conf):
        XenCertPrint("Reached StorageHandlerNFS constructor")
        self.server = storage_conf['server']
        self.serverpath = storage_conf['serverpath']        
        StorageHandler.__init__(self, storage_conf)
	
    def Create(self):
	device_config = {}
	device_config['server'] = self.server
	device_config['serverpath'] = self.serverpath
	retVal = True
	try:
	    # Create an SR
	    Print("      Creating the SR.")
	     # try to create an SR with one of the LUNs mapped, if all fails throw an exception
	    XenCertPrint("The SR create parameters are %s, %s" % (util.get_localhost_uuid(self.session), device_config))
	    sr_ref = self.session.xenapi.SR.create(util.get_localhost_uuid(self.session), device_config, '0', 'XenCertTestSR', '', 'nfs', '',False, {})
	    XenCertPrint("Created the SR %s using device_config %s" % (sr_ref, device_config))
	    displayOperationStatus(True)
            
	except Exception, e:	    
	    displayOperationStatus(False)
	    raise Exception(("   - Failed to create SR. Exception: %s" % str(e)))
		    
	if sr_ref == None:
	    displayOperationStatus(False)
	    retVal = False	
	
	return (retVal, sr_ref, device_config)
    
    def __del__(self):
        XenCertPrint("Reached StorageHandlerNFS destructor")
	StorageHandler.__del__(self)
        
    def FunctionalTests(self):
	retVal = True
	checkPoints = 0
	totalCheckPoints = 5
        testFileCreated = False
        testDirCreated = False
        mountCreated = False

	mountpoint = '/mnt/XenCertTest-' + commands.getoutput('uuidgen') 
	try:
	    # 1. Display various exports from the server for verification by the user. 
	    Print("DISCOVERING EXPORTS FROM THE SPECIFIED TARGET")
	    Print(">> This test probes the specified NFS target and displays the ")
	    Print(">> various paths exported for verification by the user. ")
	    try:
		cmd = [nfs.SHOWMOUNT_BIN, "--no-headers", "-e", self.storage_conf['server']]
		list =  util.pread2(cmd).split('\n')
		if len(list) > 0:
		    Print("   %-50s" % 'Exported Path')
		for val in list:
		    if len(val.split()) > 0:
			Print("   %-50s" % val.split()[0])
		displayOperationStatus(True)
		checkPoints += 1
	    except Exception, e:
		Print("   - Failed to display exported paths for server: %s. Exception: %s" % (self.storage_conf['server'], str(e)))
		raise e
		
	    # 2. Verify NFS target by mounting as local directory
	    Print("VERIFY NFS TARGET PARAMETERS")
	    Print(">> This test attempts to mount the export path specified ")
	    Print(">> as a local directory. ")
	    try:		
		util.makedirs(mountpoint, 755)		
		nfs.soft_mount(mountpoint, self.storage_conf['server'], self.storage_conf['serverpath'], 'tcp')
                mountCreated = True
		displayOperationStatus(True)
		checkPoints += 1
	    except Exception, e:	        
		raise Exception("   - Failed to mount exported path: %s on server: %s, error: %s" % (self.storage_conf['server'], self.storage_conf['serverpath'], str(e)))       
	    
	    # 2. Create directory and execute Filesystem IO tests
	    Print("CREATE DIRECTORY AND PERFORM FILESYSTEM IO TESTS.")
	    Print(">> This test creates a directory on the locally mounted path above")
	    Print(">> and performs some filesystem read write operations on the directory.")
	    try:
		testdir = os.path.join(mountpoint, 'XenCertTestDir-%s' % commands.getoutput('uuidgen'))
		try:
		    os.mkdir(testdir, 755)
		except Exception,e:		    
		    raise Exception("Exception creating directory: %s" % str(e))
                testDirCreated = True
		testfile = os.path.join(testdir, 'XenCertTestFile-%s' % commands.getoutput('uuidgen'))
		cmd = ['dd', 'if=/dev/zero', 'of=%s' % testfile, 'bs=1M', 'count=1', 'oflag=direct']
		(rc, stdout, stderr) = util.doexec(cmd, '')
                testFileCreated = True
		if rc != 0:		    
		    raise Exception(stderr)
		displayOperationStatus(True)
		checkPoints += 1
	    except Exception, e:
		Print("   - Failed to perform filesystem IO tests.")
		raise e	
	    
	    # 3. Report Filesystem target space parameters for verification by user
	    Print("REPORT FILESYSTEM TARGET SPACE PARAMETERS FOR VERIFICATION BY THE USER")
	    try:
		Print("  - %-20s: %s" % ('Total space', util.get_fs_size(testdir)))
		Print("  - %-20s: %s" % ('Space utilization',util.get_fs_utilisation(testdir)))
		displayOperationStatus(True)
		checkPoints += 1
	    except Exception, e:
		Print("   - Failed to report filesystem space utilization parameters. " )
		raise e 
	except Exception, e:
	    Print("   - Functional testing failed with error: %s" % str(e))
	    retVal = False   

        # Now perform some cleanup here
	try:
            if testFileCreated:
	    	os.remove(testfile)
	    if testDirCreated:
		os.rmdir(testdir)
	    if mountCreated:
		nfs.unmount(mountpoint, True)
	    checkPoints += 1
	except Exception, e:
	    Print("   - Failed to cleanup after NFS functional tests, please delete the following manually: %s, %s, %s. Exception: %s" % (testfile, testdir, mountpoint, str(e)))
	    
        return (retVal, checkPoints, totalCheckPoints)   
    
    def MPConfigVerificationTests(self):
        return (True, 1, 1)
	
    def PoolTests(self):
        return (True, 1, 1) 

