#define _GNU_SOURCE
#include <unistd.h>
#include <xs.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
#include <syslog.h>
#include <sys/types.h>
#include <signal.h>
#include <stdlib.h>
#include <fcntl.h>
#include <malloc.h>
#define MAXDIRBUF 4096
const int MIN_BLK_SIZE = 512;

int remove_base_watch(struct xs_handle *h)
{
	if (!xs_unwatch(h, "/vss", "vss"))
		return -EINVAL;
	return 0;
}

int register_base_watch(struct xs_handle *h)
{
	if (!xs_watch(h, "/vss", "vss"))
		return -EINVAL;
	return 0;
}

int xs_exists(struct xs_handle *h, const char *path)
{
        char **d;
        unsigned int num;
        xs_transaction_t xth;

        if ((xth = xs_transaction_start(h)) == XBT_NULL) {
                printf("unable to start xs trasanction\n");
                return 0;
        }

        d = xs_directory(h, xth, path, &num);
        xs_transaction_end(h, xth, 0);
        if (!d)
                return 0;

        free(d);
        return 1;
}

char *dirlist(struct xs_handle *h, const char *path)
{
        char **d, *p, *ptr;
        xs_transaction_t xth;
	unsigned int num, j=0, count = 0;

	if ((p = calloc(1,MAXDIRBUF))==NULL) {
		printf("unable to allocate memory\n");
		return NULL;
	}
        if ((xth = xs_transaction_start(h)) == XBT_NULL) {
                printf("unable to start xs trasanction\n");
                return p;
        }

        d = xs_directory(h, xth, path, &num);
        xs_transaction_end(h, xth, 0);
        if (!d)
                return p;

	ptr = p;	
        while(j < num) {
		ptr = p + count;
		if ((count + strlen(d[j]) + 1) > MAXDIRBUF) {
			printf("Reached max dir entry\n");
			return p;
		}
		if (j) {
			*ptr = '|';
			*ptr++;
			count++;
		}
		count += sprintf(ptr, d[j]);
		j++;
	}

        free(d);
        return p;	
}

char *getval(struct xs_handle *h, const char *path)
{
	char *p = NULL;
	xs_transaction_t xth;
	unsigned int len;

        if ((xth = xs_transaction_start(h)) == XBT_NULL) {
                printf("unable to start xs trasanction\n");
                return p;
        }
	p = xs_read(h, xth, path, &len);
	xs_transaction_end(h, xth, 0);
	return p;
}

int setval(struct xs_handle *h, const char *path, const char *val)
{
        int ret = 0;
	xs_transaction_t xth;
	unsigned int len;

        if ((xth = xs_transaction_start(h)) == XBT_NULL) {
                printf("unable to start xs trasanction\n");
                return ret;
        }
	len = strlen(val);
	ret = (xs_write(h, xth, path, val, len)? 1 : 0);
	xs_transaction_end(h, xth, 0);
	return ret;
}

int remove_xs_entry(struct xs_handle *h, char *dom_uuid, char *dom_path)
{
	char *path = NULL;
	int ret = 0;

	if (asprintf(&path, "/vss/%s/%s",dom_uuid, dom_path)==-1)
		goto out;

	if (xs_exists(h, path)) {
		if (!xs_rm(h, XBT_NULL, path)) {
			printf("Failed to remove xs entry %s\n", path);
			goto out;
		}
	}
	ret = 1;
 out:
	free(path);
	return ret;
}

int generic_remove_xs_entry(struct xs_handle *h, char *path)
{
	int ret = 0;

	if (xs_exists(h, path)) {
		if (!xs_rm(h, XBT_NULL, path)) {
			printf("Failed to remove xs entry %s\n", path);
			goto out;
		}
	}
	ret = 1;
 out:
	return ret;
}

char *
control_handle_event(struct xs_handle *h)
{
	unsigned int num;
	char **res, *path;

	res = xs_read_watch(h, &num);
	if (!res)
		return NULL;
	return res[XS_WATCH_PATH];
}

char *xs_file_read(char *path, int offset, int bytesToRead)
{
	char *value = memalign(MIN_BLK_SIZE,  bytesToRead);
	int fd = open( path, O_RDONLY | O_DIRECT);
	if(fd == -1)
	{
		printf("File open failed: %d" , errno);
		return "";
	}
	lseek(fd, offset, 0);
	int count = read(fd, value, bytesToRead);
	if(count == -1)
		printf("Error reading file %s, error: %d" , path, errno);
	else
		printf("count: %d, value: %s", count, value);
	close(fd);
	return value;
}
