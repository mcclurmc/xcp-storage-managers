#!/usr/bin/python
# Copyright (C) 2006-2007 XenSource Ltd.
# Copyright (C) 2008-2009 Citrix Ltd.
#
# This program is free software; you can redistribute it and/or modify 
# it under the terms of the GNU Lesser General Public License as published 
# by the Free Software Foundation; version 2.1 only.
#
# This program is distributed in the hope that it will be useful, 
# but WITHOUT ANY WARRANTY; without even the implied warranty of 
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the 
# GNU Lesser General Public License for more details.
#
# Functions to read and write SR metadata 
#
from xml.dom import minidom, Node
import struct
import util
from metadata import HDR_STRING, XML_TAG, _parseXML, MD_MAJOR, MD_MINOR, \
    retrieveXMLfromFile
import os
import mmap
import sys
sys.path.insert(0,'/opt/xensource/sm/snapwatchd')
from xslib import xs_file_read

SECTOR_SIZE = 512
XML_HEADER = "<?xml version=\"1.0\" ?>"
SECTOR2_STRUCT = "%ds%ds%ds" % ( len(XML_HEADER),
                                   49, # UUID
                                   30) # ALLOCATION
MAX_METADATA_LENGTH_SIZE = 10
LEN_FMT = "%" + "-%ds" % MAX_METADATA_LENGTH_SIZE
SECTOR_STRUCT = "%-512s" 
UUID_TAG = 'uuid'
ALLOCATION_TAG = 'allocation'
NAME_LABEL_TAG = 'nl'
NAME_DESCRIPTION_TAG = 'nd'
VDI_TAG = 'vdi'
VDI_CLOSING_TAG = '</%s>' % VDI_TAG
OFFSET_TAG = 'offset'
VDI_SECTOR_1 = "<%s><%s>%s</%s><%s>%s</%s>" % (VDI_TAG,
                                               NAME_LABEL_TAG,
                                               '%s',
                                               NAME_LABEL_TAG,
                                               NAME_DESCRIPTION_TAG,
                                               '%s',
                                               NAME_DESCRIPTION_TAG)
MAX_VDI_NAME_LABEL_DESC_LENGTH = SECTOR_SIZE - 2*len(NAME_LABEL_TAG) - \
    2*len(NAME_DESCRIPTION_TAG) - len(VDI_TAG) - 12
VDI_DELETED_TAG = 'deleted'
VDI_GEN_INFO_TAG_LIST = [VDI_DELETED_TAG, 'uuid', 'is_a_snapshot', \
                         'snapshot_of', 'snapshot_time','type','vdi_type', \
                         'read_only', 'managed','metadata_of_pool']
ATOMIC_UPDATE_PARAMS_AND_OFFSET = {NAME_LABEL_TAG: 2,
                                        NAME_DESCRIPTION_TAG: 3}
SR_INFO_SIZE_IN_SECTORS = 4
VDI_INFO_SIZE_IN_SECTORS = 2
VDI_GEN_INFO_OFFSET = 1
STRUCT_PAD_CHAR = ' '
HEADER_SEP = ':'
SECTOR_FMT = "%s%s%s%s%s%s%s" % (HDR_STRING,
                                   HEADER_SEP,
                                   LEN_FMT,
                                   HEADER_SEP,
                                   str(MD_MAJOR),
                                   HEADER_SEP,
                                   str(MD_MINOR)
                                   )
def requiresUpgrade(path):
    try:
        sector1 = xs_file_read(path, 0, SECTOR_SIZE).strip()                
        hdr = unpackHeader(sector1)
        mdmajor = hdr[2]
        mdminor = hdr[3]
            
        if mdmajor < MD_MAJOR:
            return True
            
        if mdmajor == MD_MAJOR and mdminor < MD_MINOR:
            return True
        
        return False
    except Exception, e:
        util.SMlog("Exception checking header version, upgrading metadata. " \
                   "Error: %s" % str(e))
        return True
        
def buildHeader(len):
    # build the header, which is the first sector    
    output = SECTOR_FMT % len    
    return output

def unpackHeader(input):    
    vals = input.split(HEADER_SEP)
    return (vals[0], vals[1], vals[2], vals[3])

# opens file with the right modes for 512 mem aligned sector writing
def openFileForWrite(path):
    # A write should complete before we move to the next write
    return os.open(path, os.O_RDWR | os.O_CREAT | os.O_SYNC)
    
def closeFileForWrite(fd):
    os.close(fd)
    
def readFileODirect(path, offset, length):
    return xs_file_read(path, offset, length)

def getSector(str):
    sector = SECTOR_STRUCT % str
    return sector
    
def getSectorAlignedXML(tagName, value):
    # truncate data if we breach the 512 limit
    if len("<%s>%s</%s>" % (tagName, value, tagName)) > SECTOR_SIZE:
        value = value[:SECTOR_SIZE - 2*len(tagName) - 5]
        
    return "<%s>%s</%s>" % (tagName, value, tagName)
    
def getXMLTag(tagName):
        return "<%s>%s</%s>" % (tagName, '%s', tagName)
        
def updateLengthInHeader( path, delta, decrement = False):
    try:
        try:
            fd = openFileForWrite(path)
            os.lseek(fd, 4, 0)
            
            if decrement:
                newLength = getMetadataLength(path) - delta
            else:
                newLength = getMetadataLength(path) + delta
            
            newheader = buildHeader(newLength)
            os.lseek(fd, 0, 0)
            os.write(fd, getSector(newheader))
        except Exception, e:
            util.SMlog("Exception updating metadata length info: %s." \
                       "Error: %s" % str(e))
            raise 
    finally:
        closeFileForWrite(fd)
    
def getMetadataLength(path):    
    try:
        sector1 = readFileODirect(path, 0, SECTOR_SIZE)
        lenstr = sector1.split(HEADER_SEP)[1]
        len = int(lenstr.strip(' '))
        return len
    except Exception, e:
        util.SMlog("Exception getting metadata length." \
                   "Error: %s" % str(e))
        raise 

# This function assumes the file has been opened with the right permissions
# and that the file pointer is set at the right place in the file
# it only worries about writing the VDI info in the pointed place.
def writeVdiInfo(fd, Dict, onlyFirstSector = False):
    try:
        if len(Dict[NAME_LABEL_TAG]) + len(Dict[NAME_LABEL_TAG]) > \
            MAX_VDI_NAME_LABEL_DESC_LENGTH:
            if len(Dict[NAME_LABEL_TAG]) > MAX_VDI_NAME_LABEL_DESC_LENGTH/2:
                Dict[NAME_LABEL_TAG].truncate(MAX_VDI_NAME_LABEL_DESC_LENGTH/2)
            
            if len(Dict[NAME_DESCRIPTION_TAG]) > \
                MAX_VDI_NAME_LABEL_DESC_LENGTH/2: \
                Dict[NAME_DESCRIPTION_TAG].\
                truncate(MAX_VDI_NAME_LABEL_DESC_LENGTH/2)
                
        # Fill the open struct and write it            
        os.write(fd, getSector(VDI_SECTOR_1 % (Dict[NAME_LABEL_TAG], \
                                              Dict[NAME_DESCRIPTION_TAG])))
        
        if not onlyFirstSector: 
            # Fill the VDI information and write it
            VDI_INFO_FMT = ''
            for tag in VDI_GEN_INFO_TAG_LIST:
                VDI_INFO_FMT += getXMLTag(tag)
                
            VDI_INFO_FMT += VDI_CLOSING_TAG
            
            os.write(fd, getSector(VDI_INFO_FMT % ('0',
                                                Dict['uuid'],
                                                Dict['is_a_snapshot'],
                                                Dict['snapshot_of'],
                                                Dict['snapshot_time'],
                                                Dict['type'],
                                                Dict['vdi_type'],
                                                Dict['read_only'],
                                                Dict['managed'],
                                                Dict['metadata_of_pool'])))
        
        return True

    except Exception, e:
        util.SMlog("Exception writing vdi info: %s. Error: %s" % (Dict, str(e)))
        raise        
    
def spaceAvailableForVdis(path, count):
    try:
        created = False
        try:
            # The easiest way to do this, is to create a dummy vdi and write it
            uuid = util.gen_uuid()
            vdi_info = { 'uuid': uuid,
                        NAME_LABEL_TAG: 'dummy vdi for space check',
                        NAME_DESCRIPTION_TAG: 'dummy vdi for space check',
                        'is_a_snapshot': 0,
                        'snapshot_of': '',
                        'snapshot_time': '',                                
                        'type': 'user',
                        'vdi_type': 'vhd',
                        'read_only': 0,
                        'managed': 0,
                        'metadata_of_pool': ''
            }
    
            created = addVdi(path, vdi_info)
        except IOError, e:
            raise       
    finally:
        if created:
            deleteVdi(path, uuid)

def addVdi(path, Dict):
    try:
        try:
            md = getMetadata(path)
            offset = 0
            for key in md.keys():
                if util.exactmatch_uuid(key):
                    if md[key][VDI_DELETED_TAG] == '1':
                        offset = md[key][OFFSET_TAG]
                        break
            
            fd = openFileForWrite(path)
            
            if offset == 0:
                offset = getMetadataLength(path)
                os.lseek(fd, offset, 0)
                writeVdiInfo(fd, Dict)
                # update the metadata length in the header
                updateLengthInHeader( path, SECTOR_SIZE * \
                                        VDI_INFO_SIZE_IN_SECTORS)
            else:
                os.lseek(fd, offset, 0)
                writeVdiInfo(fd, Dict)
                
            return True
    
        except Exception, e:
            util.SMlog("Exception adding vdi with info: %s. Error: %s" % \
                       (Dict, str(e)))
            raise
    finally:
        closeFileForWrite(fd)
    
# This should be called only in the cases where we are initially writing
# metadata, the function would expect a dictionary which had all information
# about the SRs and all its VDIs.
# This is the only function which expects the VDI keys to be 'vdi_UUID'
def writeMetadata(path, Dict):
    try:
        try:
            fd = openFileForWrite(path)
            
            # Fill up the first sector and write it
            uuid = getXMLTag(UUID_TAG) % Dict['uuid']
            allocation = getXMLTag(ALLOCATION_TAG) % Dict['allocation']
            first = buildHeader(SECTOR_SIZE)
            os.write(fd, getSector(first))
            
            second = struct.pack(SECTOR2_STRUCT,
                                XML_HEADER, 
                                uuid,
                                allocation
                                )
            os.write(fd, getSector(second))           
            updateLengthInHeader( path, SECTOR_SIZE)
            
            # Fill up the SR name_label and write it
            os.write(fd, getSector(getSectorAlignedXML(NAME_LABEL_TAG, \
                                                  Dict[NAME_LABEL_TAG])))
            updateLengthInHeader( path, SECTOR_SIZE)
            
            # Fill the name_description and write it
            os.write(fd, getSector(getSectorAlignedXML(NAME_DESCRIPTION_TAG, \
                                                  Dict[NAME_DESCRIPTION_TAG])))
            updateLengthInHeader( path, SECTOR_SIZE)
        finally:
            closeFileForWrite(fd)
        
        # Go over the VDIs passed and for each:
        index = 0
        for key in Dict.keys():
            if util.exactmatch_uuid(key):
                index += 1
                Dict[key]['uuid'] = key                
                addVdi(path,Dict[key])            
        return
    except Exception, e:
        util.SMlog("Exception writing metadata with info: %s. Error: %s" % \
                       (Dict, str(e)))
        raise
    
def getMetadata(path, deletedVdis = False):    
    try:
        try:
            # First check if the metadata is of a previous version
            # maybe removed from next release
            xml = retrieveXMLfromFile(path)
            return _parseXML(xml)
        except:
            Dict = {}        
            length = getMetadataLength(path)
            
            # First read in the SR details
            metadata = ''
            metadata = readFileODirect(path, 0, length)
            
            # At this point we have the complete metadata in metadata
            offset = SECTOR_SIZE + len(XML_HEADER)
            sr_info = metadata[offset: SECTOR_SIZE * 4]
            offset = SECTOR_SIZE * 4
            sr_info = sr_info.replace('\x00','')
            
            parsable_metadata = '%s<%s>%s</%s>' % (XML_HEADER, XML_TAG, \
                                                   sr_info, XML_TAG)
            
            Dict = _parseXML(parsable_metadata)
            
            # Now look at the VDI objects             
            while offset < length:
                vdi_info = metadata[offset: \
                                offset + \
                                (SECTOR_SIZE * VDI_INFO_SIZE_IN_SECTORS)]
                vdi_info = vdi_info.replace('\x00','')
                parsable_metadata = '%s<%s>%s</%s>' % (XML_HEADER, XML_TAG, \
                                               vdi_info, XML_TAG)
                vdi_info_map = _parseXML(parsable_metadata)[VDI_TAG]
                vdi_info_map[OFFSET_TAG] = offset
                Dict[vdi_info_map[UUID_TAG]] = vdi_info_map
                offset += SECTOR_SIZE * VDI_INFO_SIZE_IN_SECTORS
                
            if not deletedVdis:
                return removeDeletedVdis(Dict)
            else:
                return Dict
        
    except Exception, e:
        util.SMlog("Exception getting metadata. Error: %s" % str(e))
        raise         

def deleteVdi(path, vdi_uuid, offset = 0):
        if offset == 0:
            # parse the metadata, find the place where data for this VDI is
            # located
            md = getMetadata(path)        
        
            if not md.has_key(vdi_uuid):
                util.SMlog("VDI to be deleted not found in the metadata.")
                return 
            else:
                offset = md[vdi_uuid][OFFSET_TAG] + SECTOR_SIZE + \
                    len(VDI_DELETED_TAG) + 2
        
            try:
                # modify the deleted status to true
                fd = openFileForWrite(path)                
                os.lseek(fd, offset, 0)
                os.write(fd, '1')
                
                # if this is the last VDI in the metadata
                length = getMetadataLength(path)
                offset = md[vdi_uuid][OFFSET_TAG]
                vdi_size = SECTOR_SIZE * VDI_INFO_SIZE_IN_SECTORS
                if (length - offset) == vdi_size:
                    # update the length to be one VDI info less
                    updateLengthInHeader( path, vdi_size, True)
            finally:
                closeFileForWrite(fd)                
        return

def updateSR(path, Dict):
    util.SMlog('entering updateSR')
    offset = 0
    
    # figure out what part of the SR information is being updated
    Dict_keys = Dict.keys()
    for key in Dict:
        if key in ATOMIC_UPDATE_PARAMS_AND_OFFSET:
            # Now update the params in separate atomic operations
            try:
                fd = openFileForWrite(path)        
                os.lseek(fd, offset + SECTOR_SIZE * \
                           ATOMIC_UPDATE_PARAMS_AND_OFFSET[key], 0)
                os.write(fd, getSector(getXMLTag(key) % Dict[key]))
                Dict_keys.remove(key)
            finally:
                closeFileForWrite(fd)

    if len(Dict_keys):
        raise Exception("SR Update operation not supported for \
                            parameters: %s" % diff)

def updateVdi(path, Dict):
    util.SMlog('entering updateVdi')
    md = getMetadata(path)
    
    if not md.has_key(Dict['uuid']):
        raise Exception("VDI to be updated not found in the metadata.")
        
    # find the place where the data for this
    # VDI is located                
    offset = md[Dict['uuid']][OFFSET_TAG]
    
    # Now check which sector the update is in
    onlySector1 = True
    if set(Dict.keys()) - set([NAME_LABEL_TAG, NAME_DESCRIPTION_TAG]) != \
        set([]):
        onlySector1 = False
        
    # Now set the values
    for key in Dict.keys():
        if md[Dict['uuid']].has_key(key):
            md[Dict['uuid']][key] = Dict[key]
    
    try:
        fd = openFileForWrite(path)
        os.lseek(fd, offset, 0)
        writeVdiInfo(fd, md[Dict['uuid']], onlySector1)
    finally:
        closeFileForWrite(fd)    
            
# remove the deleted VDIs from the dictionary
def removeDeletedVdis(Dict):
    for key in Dict.keys():
        if util.exactmatch_uuid(key):
            if Dict[key]['deleted'] == '1':
                del Dict[key]
    return Dict   
