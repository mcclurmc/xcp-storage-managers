#/bin/sh

IETD_CONFIG='/etc/ietd.conf'
STATIC_CONFIG_DIR='/etc/ietd_configs'

function flush_configs
{
    rm -f $IETD_CONFIG
    for val in `ls ${STATIC_CONFIG_DIR}`; do
	cat "${STATIC_CONFIG_DIR}/${val}" >> $IETD_CONFIG
    done
}

flush_configs
