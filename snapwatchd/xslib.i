%module xslib
%{
#include <xs.h>
%}


/*Core Xen utilities*/
struct xs_handle *xs_daemon_open(void);
void xs_daemon_close(struct xs_handle *h);
int xs_fileno(struct xs_handle *h);

int remove_base_watch(struct xs_handle *h);
int register_base_watch(struct xs_handle *h);
int xs_exists(struct xs_handle *h, const char *path);
char *getval(struct xs_handle *h, const char *path);
int setval(struct xs_handle *h, const char *path, const char *val);
char *dirlist(struct xs_handle *h, const char *path);
int remove_xs_entry(struct xs_handle *h, char *dom_uuid, char *dom_path);
int generic_remove_xs_entry(struct xs_handle *h, char *path);
char *control_handle_event(struct xs_handle *h);
long get_min_blk_size(int fd);
int open_file_for_write(char *path);
int open_file_for_read(char *path);
void xs_file_write(int fd, int offset, int blocksize, char* data, int length);
char *xs_file_read(int fd, int offset, int bytesToRead);
void close_file(int fd);
