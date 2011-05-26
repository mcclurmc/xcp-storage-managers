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
import CSLGMetadata
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

# simple tracer
def report(predicate, condition):
    if predicate != condition:
        Print("Condition Failed, check SMlog")

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
                
            
            # Calculate the number of active paths here
            self.initialActivePaths = 0
            for tuple in self.listPathConfig:
                if tuple[1] == 'active':
                    self.initialActivePaths += 1
            
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

    def DataIntegrityTests(self):
        retVal = True
        checkPoint = 0
        totalCheckPoints = 14
        
        vm_uuid = StorageHandlerUtil._get_localhost_uuid()
        XenCertPrint("Got vm_uuid as %s" % vm_uuid)
        vm_ref = self.session.xenapi.VM.get_by_uuid(vm_uuid)
        sr_ref = None
        vdi_ref = None
        snap_ref = None

        try:
            ###  Resize ###
            #1)Create SR
            (retVal, sr_ref, dconf) = self.Create_SR()
            Print("Create SR, status:%s"%(retVal))
            if retVal:
                #2)Create a 4GB VDI
                (retVal, vdi_ref) = self.Create_VDI(sr_ref, 4*StorageHandlerUtil.GiB)
                Print("Create 4GB VDI, status:%s"%(retVal))
                if retVal:
                    #3)Attach the VDI to dom0
                    checkPoint += 1
                    retVal = StorageHandlerUtil.Attach_VDI(self.session, vdi_ref, vm_ref)[0]
                    Print("Attached the VDI to dom0")
                    if retVal:
                        checkPoint += 1
                    else:
                        # attach VDI failed
                        displayOperationStatus(False)
                else:
                    # create VDI failed
                    displayOperationStatus(False)
            else:
                # create SR failed
                displayOperationStatus(False)
        except Exception, e:
            Print("Exception happened while performing data IO tests. %s"%e)
            retVal = False

        try:
            #4)write known pattern to VDI
            StorageHandlerUtil.WriteDataToVDI(self.session, vdi_ref, 0, 3, skipLevel=0, full=False)
            Print("Wrote data to VDI")
            checkPoint += 1
            
            #5)detach the VDI
            StorageHandlerUtil.Detach_VDI(self.session, vdi_ref)
            Print("Detached from dom0")
            checkPoint += 1

            #6)resize the vdi to 8GB
            if self.Resize_VDI(vdi_ref, 8*StorageHandlerUtil.GiB):
                 Print("Resized the VDI to 8GB")
                 checkPoint += 1
            else:
                Print("VDI resize to 8GB Failed")
                raise Exception("VDI resize failed")

            #7)attach vdi to dom0
            retVal = StorageHandlerUtil.Attach_VDI(self.session, vdi_ref, vm_ref)[0]
            if retVal:
                checkPoint += 1
                Print("VDI attached again to Dom0")
                pass
            else:
                Print("VDI attach failed")
                raise Exception("VDI Attach Failed")

            #8)validate the pattern on first 4GB
            StorageHandlerUtil.VerifyDataOnVDI(self.session, vdi_ref, 0, 3, skipLevel=0, full=False)
            Print("Verified data onto the VDI")
            checkPoint += 1

            #9)write known pattern to second 4GB chunk
            StorageHandlerUtil.WriteDataToVDI(self.session, vdi_ref, 4, 7, skipLevel=0, full=False)
            Print("Wrote data onto grown portion of the VDI")
            checkPoint += 1

            #10)perform online resize of VDI to 12GB
            ##Not supported

            #11)detach and reattach the VDI
            StorageHandlerUtil.Detach_VDI(self.session, vdi_ref)
            retVal = StorageHandlerUtil.Attach_VDI(self.session, vdi_ref, vm_ref)[0]
            Print("Detached and Attached the VDI to Dom0")
            if not retVal:
                raise Exception("VDI Attach Failed") 
            checkPoint += 1

            #12)validate pattern on first and second 4GB chunks
            StorageHandlerUtil.VerifyDataOnVDI(self.session, vdi_ref, 0, 7, skipLevel=0, full=False)
            Print("Verified data on complete VDI")
            checkPoint += 1

            #13)create clone/snapshot VDI
            (retVal, snap_ref) = self.Snapshot_VDI(vdi_ref)
            if not retVal:
                raise Exception("VDI Snapshot Failed")
            Print("Snapshot of the VDI created")
            checkPoint += 1

            #14)verify snapshot data matches original VDI
            retVal = StorageHandlerUtil.Attach_VDI(self.session, snap_ref, vm_ref)[0]
            if not retVal:
                raise Exception("Clone/Snapshot VDI Attach Failed")
            StorageHandlerUtil.VerifyDataOnVDI(self.session, snap_ref, 0, 7, skipLevel=0, full=False)
            Print("Verified data on complete VDI")
            checkPoint += 1

            #15)destroy orignal VDI and check snapshot VDI still valid
            StorageHandlerUtil.Detach_VDI(self.session, vdi_ref)
            retVal = self.Destroy_VDI(vdi_ref)
            if not retVal:
                raise Exception("VDI Destroy Failed")
            Print("VDI destroyed successfully")
            checkPoint += 1

            #16)zero out snapshot VDI and verify
            StorageHandlerUtil.WriteDataToVDI(self.session, snap_ref, 0, 7, 0, False, True)
            Print("Zero'ed the snapshot VDI")
            StorageHandlerUtil.VerifyDataOnVDI(self.session, snap_ref, 0, 7, 0, False, True)
            Print("Verified zero'ed snapshot VDI successfully")
            checkPoint += 1

            #17)cleanup here
            StorageHandlerUtil.Detach_VDI(self.session, snap_ref)
            retVal = self.Destroy_VDI(snap_ref)
            if not retVal:
                raise Exception("snapshot destroy failed")
            retVal = self.Destroy_SR(sr_ref)
            if not retVal:
                raise Exception("SR destroy failed")
            Print("Cleaned up, Destroyed snapshots, SR used for testing") 

        except Exception, e:
            Print("Exception happened while performing data IO tests. %s"%e)
            retVal = False
        
        return (retVal, checkPoint, totalCheckPoints)

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
                if device_config.has_key('SCSIid') and (key.find(device_config['SCSIid']) != -1):
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
            
            # Find new number of active paths
            newActivePaths = 0
            for tuple in listPathConfigNew:
                if tuple[1] == 'active':
                    newActivePaths += 1
            
            if newActivePaths < self.initialActivePaths:                            
                    return False
            return True
        except Exception, e:
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

                except Exception, e:
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
                            except Exception, e:
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
                        # If this is a root device then skip IO tests for this device.
                        if os.path.realpath(util.getrootdev()) == tuple[2]:
                            Print("     -> Skipping IO tests on device %s, as it is the root device." % tuple[2])
                            continue
                        
                        pathNo += 1
        
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

                except Exception, e:
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
                        # If this is a root device then skip IO tests for this device.
                        if os.path.realpath(util.getrootdev()) == device:
                            Print("     -> Skipping IO tests on device %s, as it is the root device." % device)
                            continue

                        pathNo += 1
                        
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

class StorageHandlerISL(StorageHandler):
    def __init__(self, storage_conf):
        XenCertPrint("Reached StorageHandlerISL constructor")
        self.file = storage_conf['file']
        self.configuration = StorageHandlerUtil.parse_xml_config(self.file)
        StorageHandler.__init__(self, storage_conf)

##
## Nota Bene: 
##
##  1) The "Meta Data" VDI need to be accomodated -- which is special for integrated StorageLink
##

    def populateVDI_XAPIFields(self, vdi_ref):
        XenCertPrint("populateVDI_XAPIFields Enter")
        try:
            dest = {}
            #commented below line, because snapshot name_label from XAPI is incorrect
            #dest['name_label'] = self.session.xenapi.VDI.get_name_label(vdi_ref)
            is_a_snapshot = str(int(self.session.xenapi.VDI.get_is_a_snapshot(vdi_ref)))
            #if is_a_snapshot == "1":
            #    dest['is_a_snapshot'] = is_a_snapshot
            #    dest['snapshot_of'] = self.session.xenapi.VDI.get_snapshot_of(vdi_ref)
            #    if dest['snapshot_of'] == 'OpaqueRef:NULL':
            #        dest['snapshot_of'] = ''
            #    else:
            #        dest['snapshot_of'] = self.session.xenapi.VDI.get_sm_config(dest['snapshot_of'])['SVID']
            dest['type'] = self.session.xenapi.VDI.get_type(vdi_ref)
            #commented below, because not all VDI seems to have this field
            #dest['vdi_type'] = self.session.xenapi.VDI.get_vdi_type(vdi_ref)
            dest['read_only'] = str(int(self.session.xenapi.VDI.get_read_only(vdi_ref)))
            dest['managed'] = str(int(self.session.xenapi.VDI.get_managed(vdi_ref)))
            dest['location'] = self.session.xenapi.VDI.get_sm_config(vdi_ref)['SVID']
        except:
            raise Exception("Error while populating VDI metadata values")
        XenCertPrint("populateVDI_XAPIFields Exit")
        return dest

    def getMetaDataRec(self, sr_ref):
        XenCertPrint("getMetaDataRec Enter")
        self.sm_config =  self.session.xenapi.SR.get_sm_config(sr_ref)
        if self.sm_config.has_key('md_svid'):
            self.md_svid = self.sm_config['md_svid']
            self.metadataVolumePath = StorageHandlerUtil._find_LUN(self.md_svid)[0]
            
            dict = CSLGMetadata.getMetadata(self.metadataVolumePath)

        XenCertPrint("getMetaDataRec Exit")
        return dict

    def checkMetadataSR(self, sr_ref, verifyFields):
        XenCertPrint("checkMetadataSR Enter")
        dict = self.getMetaDataRec(sr_ref)

        XenCertPrint("checkMetadataSR Exit")
        return
    
    def checkMetadataVDI(self, sr_ref, vdi_ref, verifyFields):
        XenCertPrint("checkMetadataVDI Enter")
        if sr_ref == None:
            sr_ref = self.session.xenapi.VDI.get_SR(vdi_ref)
        dict = self.getMetaDataRec(sr_ref)
        XenCertPrint("dict is: %s"%dict)
        vdi_uuid = self.session.xenapi.VDI.get_uuid(vdi_ref)
        XenCertPrint("verifyFields is: %s"%verifyFields)

        for key in verifyFields:
            if dict['vdi_%s'%vdi_uuid][key] != verifyFields[key]:
                raise Exception("VDI:%s key:%s Metadata:%s <> Xapi:%s doesn't match"%(vdi_uuid, key, dict['vdi_%s'%vdi_uuid][key], verifyFields[key]))

        XenCertPrint("checkMetadataVDI Exit")
        return

    #
    #  VDI related
    #
       
    def Create_VDI(self, sr_ref, size):
        XenCertPrint("Create VDI")
        vdi_rec = {}
        try:
            vdi_rec['name_label'] = "XenCertVDI-" + str(time.time()).replace(".","")
            vdi_rec['name_description'] = ''
            vdi_rec['type'] = 'user'
            vdi_rec['virtual_size'] = str(size)
            vdi_rec['SR'] = sr_ref
            vdi_rec['read_only'] = False
            vdi_rec['sharable'] = False
            vdi_rec['other_config'] = {}
            vdi_rec['sm_config'] = {}
            results = self.session.xenapi.VDI.create(vdi_rec)
            self.checkMetadataVDI(sr_ref, results, self.populateVDI_XAPIFields(results))

            return (True, results)
        except Exception, e:
            XenCertPrint("Failed to create VDI. Exception: %s" % str(e))
            return (False, "")
       
    def Resize_VDI(self, vdi_ref, size):
        XenCertPrint("Resize VDI")
        try:
            self.session.xenapi.VDI.resize(vdi_ref, str(size))
            return True
        except Exception, e:
            XenCertPrint("Failed to Resize VDI. Exception: %s" % str(e))
            return False
       
    def Snapshot_VDI(self, vdi_ref):
        XenCertPrint("Snapshot VDI")
        options = {}
        try:
            results = self.session.xenapi.VDI.snapshot(vdi_ref, options)
            self.checkMetadataVDI(None, results, self.populateVDI_XAPIFields(results))
            return (True, results)
        except Exception, e:
            XenCertPrint("Failed to Snapshot VDI. Exception: %s" % str(e))
            return (False, "")
       
    def Clone_VDI(self, vdi_ref):
        XenCertPrint("Clone VDI")
        options = {}
        try:
            results = self.session.xenapi.VDI.clone(vdi_ref, options)
            self.checkMetadataVDI(None, results, self.populateVDI_XAPIFields(results))
            return (True, results)
        except Exception, e:
            XenCertPrint("Failed to Clone VDI. Exception: %s" % str(e))
            return (False, "")
       
    def Destroy_VDI(self, vdi_ref):
        XenCertPrint("Destroy VDI")
        try:
            results = self.session.xenapi.VDI.destroy(vdi_ref)
            try:
                self.checkMetadataVDI(None, results, self.populateVDI_XAPIFields(results))
            except:
                return True
        except Exception, e:
            XenCertPrint("Failed to Destroy VDI. Exception: %s" % str(e))
            return False

    #
    #  SR related
    #
       
    def getAllPBDs(self, sr_ref):
        try:
            XenCertPrint("Getting all PBDs")
            results = self.session.xenapi.SR.get_PBDs(sr_ref)
            return results
        except Exception, e:
            XenCertPrint("Getting all PBDs failed. Exception: %s" % str(e))
            return []

    def Create_PBD(self, sr_ref, pbd_device_config):
        try:
            XenCertPrint("Creating PBD")
            Fields = {}
            Fields['host']=self.session.xenapi.host.get_by_uuid(util.get_localhost_uuid(self.session))
            Fields['device_config'] = pbd_device_config
            Fields['SR'] = sr_ref
            pbd_ref = self.session.xenapi.PBD.create(Fields)
            return pbd_ref
        except Exception, e:
            XenCertPrint("Failed to create pbd. Exception: %s" % str(e))
            return False
       
    def Unplug_PBD(self, pbd_ref):
        try:
            XenCertPrint("Unplugging PBD")
            self.session.xenapi.PBD.unplug(pbd_ref)
            return True
        except Exception, e:
            XenCertPrint("Failed to unplug PBD. Exception: %s" % str(e))
            return False
       
    def Plug_PBD(self, pbd_ref):
        try:
            XenCertPrint("Plugging PBD")
            self.session.xenapi.PBD.plug(pbd_ref)
            return True
        except Exception, e:
            XenCertPrint("Failed to plug PBD. Exception: %s" % str(e))
            return False
       
    def Destroy_PBD(self, pbd_ref):
        try:
            XenCertPrint("destroying PBD")
            self.session.xenapi.PBD.destroy(pbd_ref)
            return True
        except Exception, e:
            XenCertPrint("Failed to Destroy PBD. Exception: %s" % str(e))
            return False
        
    def Probe_SR(self, sr_ref):
        local_dconf = {}
        local_smconfig = {}
        local_dconf['adapterid'] = self.configuration['adapterid']
        local_dconf['target'] = self.configuration['target']
        local_dconf['username'] = self.configuration['username']
        local_dconf['password'] = self.configuration['password']

        # different kinds of probe based on the amount of data present in dconf
        # how should we verify?

        try:
            XenCertPrint("probing SR, target only")
            probe = self.session.xenapi.SR.probe(util.get_localhost_uuid(self.session), local_dconf, "cslg", local_smconfig)
        except Exception, e:
            # exceptions are OK
            pass

        try:
            XenCertPrint("probing SR, target + ssid")
            local_dconf['storageSystemId'] = self.configuration['ssid']
            probe = self.session.xenapi.SR.probe(util.get_localhost_uuid(self.session), local_dconf, "cslg", local_smconfig)
        except Exception, e:
            # exceptions are OK
            pass

        try:
            XenCertPrint("probing SR, target + ssid + spid")
            local_dconf['storagePoolId'] = self.configuration['spid']
            probe = self.session.xenapi.SR.probe(util.get_localhost_uuid(self.session), local_dconf, "cslg", local_smconfig)
        except Exception, e:
            # exceptions are OK
            pass

        return True

    def Forget_SR(self, sr_ref):
        XenCertPrint("Forget SR")
        try:
            pbd_list = self.getAllPBDs(sr_ref)
            for pbd_ref in pbd_list:
                self.Unplug_PBD(pbd_ref)
            for pbd_ref in pbd_list:
                self.Destroy_PBD(pbd_ref)
            self.session.xenapi.SR.forget(sr_ref)
            return True
        except Exception, e:
            XenCertPrint("Failed to Forget SR. Exception: %s" % str(e))
            return False

    def Introduce_SR(self, sr_uuid):
        XenCertPrint("Introduce SR")
        try:
            self.session.xenapi.SR.introduce(sr_uuid, 'XenCertTestSR', '', 'cslg', '', False, {})
        except Exception, e:
            XenCertPrint("Failed to Introduce the SR. Exception: %s" % str(e))
            return False
            

    def Destroy_SR(self, sr_ref):
        XenCertPrint("Destroy SR")
        try:
            pbd_list = self.getAllPBDs(sr_ref)
            for pbd_ref in pbd_list:
                self.Unplug_PBD(pbd_ref)
            self.session.xenapi.SR.destroy(sr_ref)
            return True
        except Exception, e:
            XenCertPrint("Failed to Destroy SR. Exception: %s" % str(e))
            return False

    def Create(self, shared=False):
        """ alias to create SR """
        return self.Create_SR(shared)

    def Create_SR(self, shared=False):
        XenCertPrint("Create SR")
        self.device_config = {}
        retVal = True
        sr_ref = None
        try:
            # Create an SR
            self.device_config['adapterid'] = self.configuration['adapterid']
            self.device_config['target'] = self.configuration['target']
            if self.configuration.has_key('port') and self.configuration['port'] != '0':
                self.device_config['cslport'] = self.configuration['port']
            self.device_config['storageSystemId'] = self.configuration['ssid']
            self.device_config['storagePoolId'] = self.configuration['spid']
            if self.configuration.has_key('username'):
                self.device_config['username'] = self.configuration['username']
            if self.configuration.has_key('password'):
                self.device_config['password'] = self.configuration['password']
            if self.configuration.has_key('protocol') and self.configuration['protocol'] != '':
                self.device_config['protocol'] = self.configuration['protocol']

            # skip chapuser/pass for now (wkc: fixfix)
            #if self.configuration.has_key('chapuser') != None and self.configuration.has_key('chappasswd') != None:
            #    device_config['chapuser'] = self.storage_conf['chapuser']
            #    device_config['chappassword'] = self.storage_conf['chappasswd']
            # try to create an SR with one of the LUNs mapped, if all fails throw an exception
            try:                    
                XenCertPrint("The SR create parameters are %s, %s" % (util.get_localhost_uuid(self.session), self.device_config))
                sr_ref = self.session.xenapi.SR.create(util.get_localhost_uuid(self.session), self.device_config, '0', 'XenCertTestSR', '', 'cslg', '', shared, {})
                XenCertPrint("Created the SR %s using device_config %s" % (sr_ref, self.device_config))
            except Exception, e:
                XenCertPrint("Failed to Create SR. Exception: %s" % str(e))
                    
            if sr_ref == None:
                retVal = False
        except Exception, e:
            XenCertPrint("Failed to Create SR. Exception: %s" % str(e))
            retVal = False
        
        return (retVal, sr_ref, self.device_config)
        
    def GetPathStatus(self, device_config):
        # Query DM-multipath status, reporting a) Path checker b) Path Priority handler c) Number of paths d) distribution of active vs passive paths
        try:
            self.mapIPToHost = StorageHandlerUtil._init_adapters()      
            XenCertPrint("The IP to host id map is: %s" % self.mapIPToHost) 
            
            Print(">> GetPathStatus <<TBD>>")
          
            return True

        except Exception, e:
            Print("   - Failed to get path status for device_config: %s. Exception: %s" % (device_config, str(e)))
            return False            

    def DisplayPathStatus(self):
        Print(">> DisplayPathStatus <<TBD>>")
            
    def RandomlyFailPaths(self):
        Print(">> RandomlyFailPaths <<TBD>>")

    #
    # the following tests for integrated storage link can be found here
    #
    # http://scale.uk.xensource.com/confluence/display/QA/SR+Test+Plan

    def basicTests(self, session):
        retVal = True
        checkPoints = 1
        totalCheckPoints = 1

        Print (">> ISL Basic tests <<TBD>>")
        # STC0001: Installation and Access
        # 
        #    .test for presence of SM and any corresponding SM subtype in XAPI after reboot following a fresh installation
        #    .test for expected SM capabilities
        # 
        #  << TBD >>

        return (retVal, checkPoints, totalCheckPoints)   

    def FunctionalTests(self):
        retVal = True
        checkPoints = 0
        totalCheckPoints = 0

        Print (">>> ISL Functional tests")
        # R2 - SR basic feature tests
        # STC0002: Functional SMAPI tests (gated on SM.capabilities field)
        # 
        #  * .SR.create
        #  * .SR.destroy
        #  * .SR.probe
        #    .SR.introduce
        #  * .SR.forget
        #    .SR.scan - Persistant metadata test. Forget VDI database entries and 
        #                verify they get re-instantiated with the same 
        #                parameters (including name-label and any other 
        #                metadata that should be persisted. Should also attach 
        #                VDIs and check that the xenstore_data keys are the same)
        #  * .PBD.plug then PBD.unplug shared SR on master while slave PBD unplugged
        #  * .PBD.plug then PBD.unplug shared SR on slave while master PBD unplugged
        #  * .PBD.plug then PBD.unplug shared SR on master while slave PBD missing
        #  + .PBD.plug then PBD.unplug non-shared SR on master (covered in other tests)
        #  + .PBD.plug then PBD.unplug non-shared SR on slave  (covered in other tests)
        #  * .VDI.create
        #  * .VDI.destroy
        #  * .VDI.snapshot - particularly testing that the newly created snapshot 
        #                 is independent of the original, such that deleting 
        #                 the original does not delete the snapshot.
        #  * .VDI.clone - as for snapshot.
        #  * .VDI.resize 
        #  * .VDI-shrink should raise an error
        #  * .VDI-grow by minimum block size (SR/subtype specific)+ 1, repeat 
        #                 multiple resizes in a loop
        #    .VDI-resize on running VM
        #  + .VBD.plug    (covered in other tests)
            #  + .VBD.unplug  (covered in other tests)
            #    .VDI.copy between SR and local LVM (slave-master, slave-slave, master-slave)
            #    .VDI-clone with slow copy should not lock SR
            #    .Verify sparseness support for source and destination SRs during VDI.copy 
            #                 for those SRs where it is possible to tell.
            #
            # STC0005: Verify stated limits of SR
            # 
            #    .verify supported maximum number of VDIs can be created and attached
            #    .verify supported number of snapshots can be taken and destroyed
            #    .verify when a VDI is created that it is at least as large as requested 
            #                 (i.e. odd sizes of VDIs should round up correctly)
            #    .verify default disk scheduler
            #
            # STC0006: Data integrity tests
            # 
            #    .data-integrity of resized VM (ref: TC9414) 
            #    .Create a 4GB VDI on a CVSM SR
            #    .Attach the VDI to dom0 and write a randomized (but known) pattern to the VDI
            #    .Detach the VDI from dom0
            #    .Resize the VDI to 8GB
            #    .Attach the VDI to dom0 and validate the pattern on the first 4GB
            #    .Write a pattern to the second 4GB chunk of the VDI
            #    .Perform an online resize of the VDI to 12GB
            #    .Detach and reattach the VDI
            #    .Validate the patterns on the first and second 4GB chunks
            #    .VDI.clone/snapshot integrity tests 
            #    .Add known data pattern to VDI
            #    .trigger clone and snapshot combinations
            #    .continue to write data to the original node and verify that snapshot and 
            #                 clone data matches previous known data patterns
            #    .ensure that deleting original disk does not affect the snapshot.
        #    .Zeroed disk contents: Verify new vdi is zeroed when advertised as being so. (see CA-49609)
        #    .Randomised data integrity tests, covering clone/snapshot.
        # 

        try:
            Print ("  >> SR create, SR destroy")
            totalCheckPoints += 1
            #  * .SR.create
            #  * .SR.destroy
            (retVal, sr_ref, dconf) = self.Create_SR()
            if retVal:
                self.Destroy_SR(sr_ref)
                checkPoints += 1
            displayOperationStatus(retVal)

            Print ("  >> SR create, SR probe, SR destroy")
            totalCheckPoints += 1
            #  * .SR.create
            #  * .SR.probe
            #  * .SR.destroy
            (retVal, sr_ref, dconf) = self.Create_SR()
            if retVal:
                report(self.Probe_SR(sr_ref), True)
                report(self.Destroy_SR(sr_ref), True)
                checkPoints += 1
            displayOperationStatus(retVal)

            Print ("  >> SR create, SR forget, SR introduce, SR destroy")
            totalCheckPoints += 1
            #  * .SR.create
            #  * .SR.forget
            #    .SR.introduce  (wkc fixfix -- needs to be done)
            #    .SR.destroy
            (retVal, sr_ref, dconf) = self.Create_SR()
            if retVal:
                sr_uuid = self.session.xenapi.SR.get_uuid(sr_ref)
                pbds = self.session.xenapi.SR.get_PBDs(sr_ref)
                pbd_device_config = self.session.xenapi.PBD.get_device_config(pbds[0])
                report(self.Forget_SR(sr_ref), True)
                self.Introduce_SR(sr_uuid)
                sr_ref = self.session.xenapi.SR.get_by_uuid(sr_uuid)
                pbd_ref = self.Create_PBD(sr_ref, pbd_device_config)
                self.Plug_PBD(pbd_ref)
                self.Destroy_SR(sr_ref)
                checkPoints += 1
            displayOperationStatus(retVal)

            #    .VDI.create
            #    .VDI.snapshot - particularly testing that the newly created snapshot 
            #                 is independent of the original, such that deleting 
            #                 the original does not delete the snapshot.
            #    .VDI.destroy
            Print ("  >> SR create, VDI create, VDI snapshot, VDI destroy, VDI destroy (snapshot), SR destroy")
            totalCheckPoints += 1
            (retVal, sr_ref, dconf) = self.Create_SR()
            if retVal:
                lunsizeBytes = int(self.configuration['lunsize'])
                (retVal, vdi_ref) = self.Create_VDI(sr_ref, lunsizeBytes)
                if retVal:
                    (retVal, vdi_snap_ref) = self.Snapshot_VDI(vdi_ref)
                    if retVal:
                        self.Destroy_VDI(vdi_ref)
                        # need to test to make sure snap still exists (wkc fixfix)
                        report(self.Destroy_VDI(vdi_snap_ref), True)
                        checkPoints += 1
                        displayOperationStatus(True)
                    else:
                        # snapshot failed
                        displayOperationStatus(False)
                else:
                    # create VDI failed
                    displayOperationStatus(False)
                report(self.Destroy_SR(sr_ref), True)
            else:
                # create SR failed
                displayOperationStatus(False)
                    
            # same as above, just use clone
            #    .VDI.clone - as for snapshot
            Print ("  >> SR create, VDI create, VDI clone, VDI destroy, VDI destroy (clone), SR destroy")
            totalCheckPoints += 1
            (retVal, sr_ref, dconf) = self.Create_SR()
            if retVal:
                lunsizeBytes = int(self.configuration['lunsize'])
                (retVal, vdi_ref) = self.Create_VDI(sr_ref, lunsizeBytes)
                if retVal:
                    (retVal, vdi_clone_ref) = self.Clone_VDI(vdi_ref)
                    if retVal:
                        self.Destroy_VDI(vdi_ref)
                        # need to test to make sure clone still exists (wkc fixfix)
                        report(self.Destroy_VDI(vdi_clone_ref), True)
                        checkPoints += 1
                        displayOperationStatus(True)
                    else:
                        # clone failed
                        displayOperationStatus(False)
                else:
                    # create VDI failed
                    displayOperationStatus(False)
                report(self.Destroy_SR(sr_ref), True)
            else:
                # create SR failed
                displayOperationStatus(False)

            #    .VDI.resize 
            Print ("  >> SR create, VDI create, [check size], VDI resize, [check size] VDI destroy, SR destroy")
            totalCheckPoints += 1
            (retVal, sr_ref, dconf) = self.Create_SR()
            self.sm_config =  self.session.xenapi.SR.get_sm_config(sr_ref)
            if retVal:
                lunsizeBytes = int(self.configuration['lunsize'])
                (retVal, vdi_ref) = self.Create_VDI(sr_ref, lunsizeBytes)
                if self.sm_config.has_key('supports_resize') and self.sm_config['supports_resize'] == 'True':
                    if retVal:
                        retSize = self.session.xenapi.VDI.get_virtual_size(vdi_ref)
                        Print("      Requested size is %d, actually created %d" % (lunsizeBytes, int(retSize)))
                        # checksize wkc: fixfix, newsize should be twice the size of the original, for now simply print
                        newsize = int(retSize)*2
                        retVal = self.Resize_VDI(vdi_ref, int(newsize))
                        if retVal:
                            # recheck new size wkc: fixfix
                            retSize = self.session.xenapi.VDI.get_virtual_size(vdi_ref)
                            Print("      Requested re-size is %s, actually created %s" % (str(newsize), str(retSize)))
                            report(self.Destroy_VDI(vdi_ref), True)
                            checkPoints += 1
                            displayOperationStatus(True)
                        else:
                            # resize failed
                            displayOperationStatus(False)
                    else:
                        # create VDI failed
                        displayOperationStatus(False)
                else:
                    # Resize not supported
                    report(self.Destroy_VDI(vdi_ref), True)
                    checkPoints += 1
                    displayOperationStatus(True)
                report(self.Destroy_SR(sr_ref), True)
            else:
                # create SR failed
                displayOperationStatus(False)

            #    .VDI.resize (shrink) expect an error
            Print ("  >> SR create, VDI create, VDI resize (shrink), VDI destroy, SR destroy")
            totalCheckPoints += 1
            (retVal, sr_ref, dconf) = self.Create_SR()
            if retVal:
                lunsizeBytes = int(self.configuration['lunsize'])
                (retVal, vdi_ref) = self.Create_VDI(sr_ref, lunsizeBytes)
                if retVal:
                    retSize = self.session.xenapi.VDI.get_virtual_size(vdi_ref)
                    Print("      Requested size is %d, actually created %d" % (lunsizeBytes, int(retSize)))
                    # checksize wkc: fixfix, newsize should be twice the size of the original, for now simply print
                    newsize = int(retSize)/2
                    retVal = self.Resize_VDI(vdi_ref, int(newsize))
                    if retVal == True:
                        displayOperationStatus(False)
                    else:
                        checkPoints += 1
                        displayOperationStatus(True)
                    report(self.Destroy_VDI(vdi_ref), True)
                report(self.Destroy_SR(sr_ref), True)
                retVal = True
            else:
                # create SR failed
                displayOperationStatus(False)

            #    .VDI.grow
            Print ("  >> SR create, VDI create, [check size], VDI resize (grow), [check size], repeat, VDI destroy, SR destroy")
            totalCheckPoints += 1
            (retVal, sr_ref, dconf) = self.Create_SR()
            self.sm_config =  self.session.xenapi.SR.get_sm_config(sr_ref)
            if retVal:
                lunsizeBytes = int(self.configuration['lunsize'])
                (retVal, vdi_ref) = self.Create_VDI(sr_ref, lunsizeBytes)
                if self.sm_config.has_key('supports_resize') and self.sm_config['supports_resize'] == 'True':
                    if retVal:
                        retSize = self.session.xenapi.VDI.get_virtual_size(vdi_ref)
                        Print("      Requested size is %d, actually created %d" % (lunsizeBytes, int(retSize)))
                        # checksize wkc: fixfix, newsize should be twice the size of the original, for now simply print
                        passed = 1
                        for i in range(0,10):
                            newsize = int(retSize)+int(self.configuration['growsize'])
                            retVal = self.Resize_VDI(vdi_ref, int(newsize))
                            if retVal:
                                # recheck new size wkc: fixfix
                                retSize = self.session.xenapi.VDI.get_virtual_size(vdi_ref)
                                Print("      Requested re-size is %s, actually created %s" % (str(newsize), str(retSize)))
                                displayOperationStatus(True)
                            else:
                                # resize failed
                                displayOperationStatus(False)
                                passed = 0
                        checkPoints += passed
                    else:
                        # create VDI failed
                        displayOperationStatus(False)
                else:
                    # VDI resize not supported
                    checkPoints += 1
                    displayOperationStatus(True)
                report(self.Destroy_VDI(vdi_ref), True)
                report(self.Destroy_SR(sr_ref), True)
            else:
                # create SR failed
                displayOperationStatus(False)

            # multiple host PBD plug/unplugs - requires a shared SR
            Print ("  >> Mulitple host tests")
            try:
                host_list = self.session.xenapi.host.get_all()
                if len(host_list) > 1:
                    Print ("  >> create SR, unplug all PBDs.")
                    totalCheckPoints += 1
                    (retVal, sr_ref, dconf) = self.Create_SR(True)
                    if retVal:
                        passed = 1
                        pbd_list = self.getAllPBDs(sr_ref)
                        for pbd_ref in pbd_list:
                            if self.Unplug_PBD(pbd_ref) == False:
                                passed = 0
                        checkPoints += passed
                    else:
                        # unplug failed
                        displayOperationStatus(False)

                    #    .PBD.plug then PBD.unplug shared SR on master while slave PBD unplugged
                    Print ("  >> PBD plug and unplug on master with slave PBD unplugged")
                    totalCheckPoints += 1
                    try:
                        poolref = self.session.xenapi.pool.get_all()[0]
                        masterref = self.session.xenapi.pool.get_master(poolref)
                        passed = 1
                        for pbd_ref in pbd_list:
                            if self.session.xenapi.PBD.get_host(pbd_ref) == masterref:
                                mpbd = pbd_ref
                                if self.Plug_PBD(mpbd) == False:
                                    passed = 0
                        if self.Unplug_PBD(mpbd) == False:
                            passed = 0
                        checkPoints += passed
                        displayOperationStatus(passed == 1)
                    except Exception, e:
                        # failed to operate on xapi
                        XenCertPrint("Exception: %s" % str(e))
                        displayOperationStatus(False)
                
                    #    .PBD.plug then PBD.unplug shared SR on slave while master PBD unplugged
                    Print ("  >> PBD plug and unplug on slave with master PBD unplugged")
                    totalCheckPoints += 1
                    try:
                        poolref = self.session.xenapi.pool.get_all()[0]
                        masterref = self.session.xenapi.pool.get_master(poolref)
                        passed = 1
                        for pbd_ref in pbd_list:
                            if self.session.xenapi.PBD.get_host(pbd_ref) != masterref:
                                spbd = pbd_ref
                                if self.Plug_PBD(spbd) == False:
                                    passed = 0
                                break
                        if self.Unplug_PBD(spbd) == False:
                            passed = 0
                        checkPoints += passed
                        displayOperationStatus(passed == 1)
                    except Exception, e:
                        # failed to operate on xapi
                        XenCertPrint("Exception: %s" % str(e))
                        displayOperationStatus(False)

                    #    .PBD.plug then PBD.unplug shared SR on master while slave PBD missing
                    Print ("  >> PBD plug and unplug on master with slave PBD missing")
                    totalCheckPoints += 1
                    try:
                        poolref = self.session.xenapi.pool.get_all()[0]
                        masterref = self.session.xenapi.pool.get_master(poolref)
                        passed = 1
                        # delete all but master PBD
                        for pbd_ref in pbd_list:
                            if self.session.xenapi.PBD.get_host(pbd_ref) != masterref:
                                self.session.xenapi.PBD.destroy(pbd_ref)
                        pbd_list = self.getAllPBDs(sr_ref)
                        # should only be one left
                        if len(pbd_list) != 1:
                            displayOperationStatus(False)
                        else:
                            pbd_ref = pbd_list[0]
                            if self.session.xenapi.PBD.get_host(pbd_ref) != masterref:
                                displayOperationStatus(False)
                            else:
                                if self.Plug_PBD(pbd_ref) == False:
                                    displayOperationStatus(False)
                                else:
                                    if self.Unplug_PBD(pbd_ref) == False:
                                        displayOperationStatus(False)
                                    else:
                                        checkPoints += 1
                                        displayOperationStatus(True)
                                        self.session.xenapi.PBD.destroy(pbd_ref)
                                        report(self.Forget_SR(sr_ref), True)
                    except Exception, e:
                        # failed to operate on xapi
                        XenCertPrint("Exception: %s" % str(e))
                        displayOperationStatus(False)
                else:
                    Print ("  >> Only one host detected, skipping")
            except:
                Print ("  >> Failed to get host list, skipping")
               
        except Exception, e:
            raise

        if checkPoints != totalCheckPoints:
            retVal = False
        else:
            retVal = True

        return (retVal, checkPoints, totalCheckPoints)   

    def cleanup(context):
        return
    
    def __del__(self):
        XenCertPrint("Reached StorageHandlerISL destructor")
        StorageHandler.__del__(self)
