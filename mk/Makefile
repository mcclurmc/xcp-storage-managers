include $(B_BASE)/common.mk

SM_DRIVERS := File
SM_DRIVERS += NFS
SM_DRIVERS += EXT
SM_DRIVERS += ISCSI
SM_DRIVERS += Dummy
SM_DRIVERS += udev
SM_DRIVERS += ISO
SM_DRIVERS += HBA
SM_DRIVERS += LVHD
SM_DRIVERS += LVHDoISCSI
SM_DRIVERS += LVHDoHBA
SM_DRIVERS += SHM

SM_LIBS := SR
SM_LIBS += SRCommand
SM_LIBS += VDI
SM_LIBS += cleanup
SM_LIBS += lvutil
SM_LIBS += lvmcache
SM_LIBS += util
SM_LIBS += scsiutil
SM_LIBS += vhdutil
SM_LIBS += lvhdutil
SM_LIBS += xs_errors
SM_LIBS += nfs
SM_LIBS += devscan
SM_LIBS += sysdevice
SM_LIBS += iscsilib
SM_LIBS += mpath_dmp
SM_LIBS += mpath_null
SM_LIBS += mpath_cli
SM_LIBS += mpathutil
SM_LIBS += LUNperVDI
SM_LIBS += mpathcount
SM_LIBS += refcounter
SM_LIBS += journaler
SM_LIBS += fjournaler
SM_LIBS += lock
SM_LIBS += flock
SM_LIBS += ipc
SM_LIBS += metadata
SM_LIBS += lvmanager
SM_LIBS += blktap2
SM_LIBS += mpp_mpathutil
SM_LIBS += mpp_luncheck
SM_LIBS += updatempppathd
SM_LIBS += lcache
SM_LIBS += resetvdis

SM_XML := XE_SR_ERRORCODES

SM_DEST := /opt/xensource/sm/
DEBUG_DEST := /opt/xensource/debug/
BIN_DEST := /opt/xensource/bin/
MASTER_SCRIPT_DEST := /etc/xensource/master.d/
PLUGIN_SCRIPT_DEST := /etc/xapi.d/plugins/
LIBEXEC := /opt/xensource/libexec/
CRON_DEST := /etc/cron.d/

SM_TESTS := $(MY_OUTPUT_DIR)/storage-manager-tests.tar

REPO := $(call hg_loc,sm)

PKG := $(MY_MAIN_PACKAGES)/sm.tar.bz2
PKG_STAMP := $(MY_MAIN_PACKAGES)/.dirstamp

SM_STAGING := $(MY_OBJ_DIR)/staging
SM_STAMP := $(MY_OBJ_DIR)/.staging_stamp

PEXPECT_DIST := $(CARBON_DISTFILES)/sm/pexpect-2.3.tar.gz
PEXPECT_SRC := $(MY_OBJ_DIR)/pexpect-2.3
PYTHON_DEST := $(SM_STAGING)/usr/lib/python2.4

ISA_DIST := $(CARBON_DISTFILES)/sm/iSL-patch.tar.gz
ISA_SRC := $(MY_OBJ_DIR)/iSL-patch
ISA_INSTALL := iSL-Install.sh

WINSA_DIST := $(CARBON_DISTFILES)/sm/WinSA.zip

SH := /bin/sh

.PHONY: build
build: $(PKG) $(MY_SOURCES)/MANIFEST
	@ :

$(PKG): $(PKG_STAMP) $(SM_STAMP) $(SM_TESTS)
	tar -C $(SM_STAGING) -jcf $@ .

$(SM_TESTS): $(MY_OUTPUT_DIRSTAMP)
	rm -f $@
	tar -cf $@ -C $(REPO) tests

$(SM_STAMP):
	$(call mkdir_clean,$(SM_STAGING))
	mkdir -p $(SM_STAGING)$(SM_DEST)
	mkdir -p $(SM_STAGING)$(DEBUG_DEST)
	mkdir -p $(SM_STAGING)$(BIN_DEST)
	mkdir -p $(SM_STAGING)$(MASTER_SCRIPT_DEST)
	mkdir -p $(SM_STAGING)$(PLUGIN_SCRIPT_DEST)
	mkdir -p $(SM_STAGING)/sbin
	for i in $(SM_LIBS); do \
	  install -m 755 -o root -g root $(REPO)/drivers/$$i.py \
	    $(SM_STAGING)$(SM_DEST); done
	install -m 755 -o root -g root $(REPO)/drivers/mpathHBA \
	  $(SM_STAGING)$(SM_DEST)
	for i in $(SM_DRIVERS); do \
	  install -m 755 -o root -g root $(REPO)/drivers/$${i}SR.py \
	    $(SM_STAGING)$(SM_DEST); done
	for i in $(SM_XML); do \
	  install -m 755 -o root -g root $(REPO)/drivers/$$i.xml \
	    $(SM_STAGING)$(SM_DEST); done
	cd $(SM_STAGING)$(SM_DEST) && for i in $(SM_DRIVERS); do \
	  ln -sf $$i"SR.py" $$i"SR"; \
	done
	rm $(SM_STAGING)$(SM_DEST)/SHMSR
	cd $(SM_STAGING)$(SM_DEST) && rm -f LVHDSR && ln -sf LVHDSR.py LVMSR
	cd $(SM_STAGING)$(SM_DEST) && rm -f LVHDoISCSISR && ln -sf LVHDoISCSISR.py LVMoISCSISR
	cd $(SM_STAGING)$(SM_DEST) && rm -f LVHDoHBASR && ln -sf LVHDoHBASR.py LVMoHBASR
	ln -sf $(SM_DEST)mpathutil.py $(SM_STAGING)/sbin/mpathutil
	install -m 755 -o root -g root $(REPO)/drivers/02-vhdcleanup $(SM_STAGING)$(MASTER_SCRIPT_DEST)
	install -m 755 -o root -g root $(REPO)/drivers/lvhd-thin $(SM_STAGING)$(PLUGIN_SCRIPT_DEST)
	install -m 755 -o root -g root $(REPO)/drivers/on-slave $(SM_STAGING)$(PLUGIN_SCRIPT_DEST)
	install -m 755 -o root -g root $(REPO)/drivers/testing-hooks $(SM_STAGING)$(PLUGIN_SCRIPT_DEST)
	install -m 755 -o root -g root $(REPO)/drivers/coalesce-leaf $(SM_STAGING)$(PLUGIN_SCRIPT_DEST)
	install -m 755 -o root -g root $(REPO)/drivers/nfs-on-slave $(SM_STAGING)$(PLUGIN_SCRIPT_DEST)
	install -m 755 -o root -g root $(REPO)/drivers/tapdisk-pause $(SM_STAGING)$(PLUGIN_SCRIPT_DEST)
	install -m 755 -o root -g root $(REPO)/drivers/vss_control $(SM_STAGING)$(PLUGIN_SCRIPT_DEST)
	ln -sf $(PLUGIN_SCRIPT_DEST)vss_control $(SM_STAGING)$(SM_DEST)
	install -m 755 -o root -g root $(REPO)/drivers/iscsilib.py $(SM_STAGING)$(SM_DEST)
	mkdir -p $(SM_STAGING)$(LIBEXEC)
	install -m 755 -o root -g root $(REPO)/scripts/local-device-change $(SM_STAGING)$(LIBEXEC)
	install -m 755 -o root -g root $(REPO)/scripts/check-device-sharing $(SM_STAGING)$(LIBEXEC)
	$(MAKE) -C $(REPO)/dcopy clean
	$(MAKE) -C $(REPO)/dcopy install DESTDIR=$(SM_STAGING)
	$(MAKE) -C $(REPO)/snapwatchd clean
	$(MAKE) -C $(REPO)/snapwatchd install DESTDIR=$(SM_STAGING)
	cd $(MY_OBJ_DIR) && tar zxf $(PEXPECT_DIST)
	mkdir -p $(PYTHON_DEST)
	install -m 644 -o root -g root $(PEXPECT_SRC)/pexpect.py $(PYTHON_DEST)
	ln -sf $(SM_DEST)blktap2.py $(SM_STAGING)$(BIN_DEST)/blktap2
	mkdir -p $(SM_STAGING)$(CRON_DEST)
	install -m 644 -o root -g root $(REPO)/drivers/vmprcron $(SM_STAGING)$(CRON_DEST)
	install -m 755 -o root -g root $(REPO)/drivers/runvmpr $(SM_STAGING)$(SM_DEST)
	install -m 755 -o root -g root $(REPO)/drivers/vmpr $(SM_STAGING)$(PLUGIN_SCRIPT_DEST)
	ln -sf $(SM_DEST)lcache.py $(SM_STAGING)$(BIN_DEST)tapdisk-cache-stats
#	cd $(MY_OBJ_DIR) && tar zxf $(ISA_DIST)
#	cd $(ISA_SRC) && $(SH) -c './$(ISA_INSTALL)'
#	cd $(SM_DEST) && cp -rf $(WINSA_DIST) .
	@touch $@


$(MY_SOURCES)/MANIFEST: $(MY_SOURCES_DIRSTAMP)
	echo sm attrib file $(PEXPECT_DIST) > $@

.PHONY: clean
clean:
	rm -f $(PKG_STAMP) $(PKG) $(SM_STAMP)
	rm -rf $(SM_STAGING)

