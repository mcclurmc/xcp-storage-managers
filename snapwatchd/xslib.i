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
char *xs_file_read(char *path, int offset, int bytesToRead);
