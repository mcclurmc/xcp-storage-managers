#define _GNU_SOURCE
#include <stdio.h>
#include <unistd.h>
#include <stdlib.h>
#include <fcntl.h>
#include <errno.h>
#include <string.h>
#include <time.h>
#include <libaio.h>

typedef unsigned long long blk_t;
struct qptr {
    struct qptr *next;
};


#define MAX_AIO_REQS 2
#define MIN_BLK_SIZE 512
#define MAX_DATA_POOL (MAX_AIO_REQS * block_size)
int block_size = MIN_BLK_SIZE;

static blk_t *blk_table;
static struct qptr *freedata = 0;
static struct ioreq *ioreqs = 0;
static struct ioreq *inuse_ioreqs = 0;

int count = 0;

struct ioreq {
    struct ioreq *next;
    struct iocb *iocb;
};

struct file_handle {
    int      fd;
    FILE    *fp;
    int      err;
    int      buffered;
    int      readverify;
    int      libaio;
    int      retryIO;
    io_context_t aioctx;
};

static void return_data_buf(struct qptr *buf)
{
    struct qptr *tmp;
    tmp = freedata;

    /* only needed for debug */
    while (tmp) {
        if (tmp == buf) {
            printf("oopsy! double free! (%p)\n", buf);
        }
        tmp = tmp->next;
    }

    buf->next = freedata;
    freedata = buf;
    count++;
}

static int init_data_pool(int block_size)
{
    int pool_left = MAX_DATA_POOL;
    struct qptr *buf;

    while ((pool_left - block_size) >= 0) {
        posix_memalign((void **)&buf, 512, block_size);
        if (!buf) return 1;
        memset(buf, 0, block_size);
        return_data_buf(buf);
        pool_left -= block_size;
    }
    //printf("%d data blocks\n", count);
    return 0;
}

static struct qptr *get_data_buf(void)
{
    struct qptr *buf;

    buf = freedata;
    if (!buf) return buf;

    freedata = buf->next;
    buf->next = 0;

    count--;
    return buf;
}

static void free_data_pool(void)
{
    struct qptr *buf;
    while (freedata != 0) {
        buf = get_data_buf();
        free(buf);
    }
}

static void return_ioreq(struct ioreq *ior)
{
    struct ioreq *tmp;
    tmp = ioreqs;

    /* only needed for debug */
    while (tmp) {
        if (tmp == ior) {
            printf("oopsy! double free in ioreq return! (%p)\n", ior);
        }
        tmp = tmp->next;
    }

    ior->next = ioreqs;
    ioreqs = ior;
}

static int init_ioreqs(void)
{
    int left = MAX_AIO_REQS;

    struct ioreq *ior;

    while (left--) {
        ior = malloc(sizeof(struct ioreq));
        if (!ior) return 1;
        memset(ior, 0, sizeof(ioreqs));
        ior->iocb = malloc(sizeof(struct iocb));
        if (!ior->iocb) return 1;
        memset(ior->iocb, 0, sizeof(struct iocb));
        return_ioreq(ior);
    }
    return 0;
}

static struct ioreq *get_ioreq(void)
{
    struct ioreq *ior;

    ior = ioreqs;
    if (!ior) return ior;

    ioreqs = ior->next;
    ior->next = 0;
    memset(ior->iocb, 0, sizeof(struct iocb));

    return ior;
}

static void free_ioreq_pool(void)
{
    struct ioreq *ior;
    while (ioreqs != 0) {
        ior = get_ioreq();
        if (ior && ior->iocb) free(ior->iocb);
        if (ior) free(ior);
    }
}

static void set_ioreq_inuse(struct ioreq *ior)
{
    struct ioreq *tmp;
    tmp = inuse_ioreqs;

    /* only needed for debug */
    while (tmp) {
        if (tmp == ior) {
            printf("oopsy! iorec qlready in use! (%p)\n", ior);
        }
        tmp = tmp->next;
    }

    ior->next = inuse_ioreqs;
    inuse_ioreqs = ior;
}

static struct ioreq *find_and_release_inuse_ioreq(struct iocb *iocb)
{
    struct ioreq *tmp;
    struct ioreq *prev = 0;
    tmp = inuse_ioreqs;

    while (tmp) {
        if (tmp->iocb == iocb) {
            break;
        }
        prev = tmp;
        tmp = tmp->next;
    }

    if (!tmp) {
        printf("internal error, inuse ioreq not found %p\n", iocb);
    }

    if (!prev) {
        inuse_ioreqs = tmp->next;
    } else {
        prev->next = tmp->next;
    }

    return tmp;
}

static void
fail(int err)
{
    printf("biotest failed: %d\n", err);
    exit(err);
}

static void
usage(int err)
{
    printf("usage: biotest <-t target> <-m megs> "
           "[-r random] [-a aio] [-d direct] [-b buffered] [-v readverify] "
           "[-y retryIO] <-s blocksize>\n");
    if (err)
        fail(err);
    else 
        exit(0);
}

static inline void
swap(blk_t l, blk_t r)
{
    blk_t t      = blk_table[l];
    blk_table[l] = blk_table[r];
    blk_table[r] = t;
}

static int
init_blk_table(unsigned long blks, int rand)
{
    blk_t i;

    blk_table = NULL;

    if (!rand)
        return 0;

    blk_table = malloc(blks * sizeof(blk_t));
    if (!blk_table)
        return errno;

    for (i = 0; i < blks; i++)
        blk_table[i] = i;

    if (rand) {
        for (i = 0; i < blks; i++) {
            blk_t r = lrand48() % blks;
            swap(i, r);
        }
    }

    return 0;
}

static inline unsigned long
get_blk(blk_t i)
{
    if (!blk_table)
        return i;
    return blk_table[i];
}

static inline void
set_buf(blk_t *buf, blk_t num) {
    blk_t i, n = block_size / sizeof(blk_t);
    
    for (i = 0; i < n; i++)
        buf[i] = num;
}

static inline int
check_buf(blk_t *buf, blk_t num) {
    blk_t i, j, n = block_size / sizeof(blk_t);

    for (i = 0; i < n; i++)
        if (buf[i] != num)
            goto error;

    return 0;

 error:
    fprintf(stderr, "Readback after write error\n");
    fprintf(stderr, "Block 0x%llx contains:\n", num);
    for (i = 0; i < n; i += 8) {
        for (j = 0; j < 8; j++)
            fprintf(stderr, "0x%llx ", buf[i + j]);
        fprintf(stderr, "\n");
    }

    return -1;
}

static struct file_handle
fhopen(char *target, int direct, int buffered, int libaio, int readverify)
{
    int flags;
    struct file_handle fh;

    memset(&fh, 0, sizeof(struct file_handle));

    flags = O_RDWR |O_LARGEFILE | O_CREAT | (direct ? O_DIRECT : 0);
    if (!readverify) flags |= O_TRUNC;
    fh.fd = open(target, flags, 0666);
    if (buffered)
        fh.fp = fdopen(fh.fd, (readverify ? "r" : "w+"));

    fh.err      = errno;

    if (libaio && !fh.err) {
        long status;
        memset(&fh.aioctx, 0, sizeof(fh.aioctx));
        fh.aioctx = (io_context_t)1; /* hack from blktap */
        status  = io_setup(MAX_AIO_REQS, &fh.aioctx);
        if (status) {
            fh.err = errno;
        }
    }

    return fh;
}

static inline void
fhclose(struct file_handle *fh)
{
    if (fh->buffered)
        fclose(fh->fp);
    close(fh->fd);
}

static inline int
fhseek(struct file_handle *fh, off64_t off, int whence)
{
    int fbs;
    fh->err = 0;


    if (fh->buffered) {
        fbs = fseek(fh->fp, off, whence);
        if (fbs < 0)
            fh->err = errno;
    } else
        if (lseek64(fh->fd, off, SEEK_SET) == (off64_t)-1)
            fh->err = errno;

    return fh->err;
}

static inline int
fhwrite(struct file_handle *fh, void *buf, size_t size, off64_t off)
{
    int ret;
    
    fhseek(fh, off, SEEK_SET);
    if (fh->err)
        return fh->err;

    if (fh->buffered) {
        ret = fwrite(buf, size, 1, fh->fp);
        if (ret<0) printf("write failed %d\n", ret);
        fh->err = ferror(fh->fp);
        clearerr(fh->fp);
    } else {
        ret = write(fh->fd, buf, size);
        if (ret<0) printf("write failed %d\n", ret);
        fh->err = (ret == size ? 0 : (errno ? errno : -EIO));
    }

    return fh->err;
}

static inline int
fhread(struct file_handle *fh, void *buf, size_t size, off64_t off)
{
    int ret;
    
    fhseek(fh, off, SEEK_SET);
    if (fh->err)
        return fh->err;
    
    memset(buf, 0xa5, size);
    if (fh->buffered) {
        ret = fread(buf, size, 1, fh->fp);
        if (ret<0) printf("read failed %d\n", ret);
        fh->err = ferror(fh->fp);
        clearerr(fh->fp);
    } else {
        ret = read(fh->fd, buf, size);
        fh->err = (ret == size ? 0 : (errno ? errno : -EIO));
    }

    return fh->err;
}

static int writenormal(struct file_handle *fh, blk_t blks, blk_t *buf)
{
    int tenp, progress, i;
    off64_t file_offset = 0;

    fprintf(stderr, "writing blocks: ");
    fflush(stderr);
    progress = tenp = blks / 10;

    for (i = 0; i < blks; i++) {
        blk_t blk;

        blk = get_blk(i);
        set_buf(buf, blk);

        do {
            fhwrite(fh, buf, block_size, file_offset);
            if (fh->err && fh->retryIO) {
                struct timespec tm = { .tv_sec = 1,
                               .tv_nsec = 0 };
                fprintf(stderr, "error writing blk %llx:"
                     " %d (retrying)\n", blk, fh->err);
                nanosleep(&tm, NULL);
            }
        } while (fh->err && fh->retryIO);

        file_offset += block_size;

        if (fh->err) {
            fprintf(stderr, 
                "error writing blk %llx: %d\n", 
                blk, fh->err);
            goto out;
        }

        if (i == progress) {
            fprintf(stderr, ".");
            fflush(stderr);
            progress += tenp;
        }
    }

    return 0;
out:
    return 1;
}

static blk_t *newdatareq(int size)
{
    blk_t *ret;

    ret = (blk_t *)get_data_buf();
    if (ret) memset(ret, 0, size);
    return ret;
}

int bp()
{
    printf("ha! a bp\n");
}

static int writeaio(struct file_handle *fh, blk_t blks)
{
    off64_t file_offset = 0;
    struct io_event ioevent[MAX_AIO_REQS];
    struct iocb *iocbs[MAX_AIO_REQS];
    blk_t *iodata;
    long status;
    int done_submitting = 0;
    int done = 0;
    int tsubmit = 0;
    int tcomplete = 0;
    int blkindex = 0;
    int curblk;
    struct ioreq *ior;
    blk_t blk;
    int j, k;
    int pcent[10] = {0,};

    fprintf(stderr, "writing blocks: ");
    fflush(stderr);

    while (!done) {

        memset(iocbs, 0, sizeof(iocbs));

        j = 0;

        while ((ior = get_ioreq()) && (tsubmit+j < blks)) {

            curblk = (blkindex + j);

            /* get a data buffer, and an iocb */
            memset(ior->iocb, 0, sizeof(struct iocb));
            set_ioreq_inuse(ior);

            blk = get_blk(curblk);
            iodata = newdatareq(block_size);
            if (!iodata) {
                printf("internal error ... can't find a free buffer!\n");
                goto out;
            }
            set_buf(iodata, curblk);
            iocbs[j] = ior->iocb;
   
            io_prep_pwrite(iocbs[j], 
                       fh->fd, 
                       iodata,
                       block_size, 
                       file_offset);

            //printf("prep: %d --> fh=%d buf=%p size=%d offset=%llu\n", j, fh->fd, iodata, block_size, file_offset);
            file_offset += block_size;
            j++;
        }
        blkindex += j;

        if (j > 0) {
            //printf("sumitting %d aio blocks\n", j);
            /* submitting aio */
            status = io_submit(fh->aioctx, j, iocbs);
            if (status < 0) {
                perror("io_submit failed");
                goto out;
            }

            tsubmit += j;

            if (status != j) {
                goto out;
            }
        } 

        /* process outstanding IO return events */
        memset(ioevent, 0, sizeof(ioevent));
        status = io_getevents(fh->aioctx, 0, MAX_AIO_REQS, ioevent, NULL);

        if (status >= 0) {
            int bucket;
            int l;
            bucket = ((10*tcomplete)/blks);
            l = bucket;
            while (l>0 && pcent[l]==0) {
                pcent[l--] = 1;
                fprintf(stderr, ".");
            }
        }

        for(k=0; k<status; k++) {
            struct ioreq *iorec;
            if (ioevent[k].res != block_size) {
                if (((signed)ioevent[k].res == -EIO) && fh->retryIO) {
                     /* we got an expected IO error, resubmit! */
                     status = io_submit(fh->aioctx, 1, &ioevent[k].obj);
                } else {
                     printf("IO failed: results=%d, expected=%d\n", 
                                        ioevent[k].res, block_size);
                }
            } else {
                tcomplete += 1;
                /* return data buffer */
                if (ioevent[k].obj->u.c.buf) {
                    return_data_buf((struct qptr *)
                           ioevent[k].obj->u.c.buf);
                }
                ioevent[k].obj->u.c.buf = 0;
                /* returning iocb */
                iorec = find_and_release_inuse_ioreq(ioevent[k].obj);
                return_ioreq(iorec);
            }
        }

        done = ((tsubmit == blks) && (tsubmit == tcomplete));
    }
    return 0;

out:
    printf("bail in aiowrite...\n");
    return 1;
}

int
main(int argc, char **argv)
{
    char  *target;
    struct file_handle fh;
    int    c, err, rand, direct, buffered, readverify, retryIO, bs;
    int    libaio, status;
    blk_t  i, progress, tenp, megs, blks, *buf;

    err       = 0;
    megs      = 0;
    blks      = 0;
    rand      = 0;
    direct    = 0;
    buffered  = 0;
    readverify= 0;
    retryIO   = 0;
    libaio    = 0;
    target    = NULL;
    off64_t file_offset = 0;

    while ((c = getopt(argc, argv, "t:rm:dbh:vys:a")) != -1) {
        switch (c) {
        case 'a':
            libaio = 1;
            break;
        case 'b':
            buffered = 1;
            break;
        case 'd':
            direct = 1;
            break;
        case 'h':
            usage(0);
        case 't':
            target = optarg;
            break;
        case 'm':
            megs = strtoul(optarg, NULL, 10);
            break;
        case 'r':
            rand = 1;
            break;
        case 's':
            bs = strtoul(optarg, NULL, 10);
            if (bs > block_size) {
                block_size = bs & ~(MIN_BLK_SIZE-1);
            }
            break;
        case 'v':
            readverify = 1;
            break;
        case 'y':
            retryIO = 1;
            break;
        case '?':
            fprintf(stderr, "Unrecognized option: -%c\n", optopt);
            usage(EINVAL);
        }
    }

    blks = megs*1024*1024 / block_size;
    printf("block size    =%d\n", block_size);
    printf("data set (mb) =%d\n", megs);
    printf("blocks        =%d\n", blks);

    if (!target || !megs)
        usage(EINVAL);

    if (direct && buffered) {
        fprintf(stderr, "'-d' and '-b' are "
            "mutually exclusive options\n");
        usage(EINVAL);
    }

#if 0
    if (libaio && buffered) {
        fprintf(stderr, "'-a' and '-b' are "
            "mutually exclusive options\n");
        usage(EINVAL);
    }
#endif

    if (!readverify && init_blk_table(blks, rand)) {
        fprintf(stderr, "error allocating block table\n");
        fail(ENOMEM);
    }


    fh = fhopen(target, direct, buffered, libaio, readverify);
    if (fh.err) {
        fprintf(stderr, "error opening %s: %d\n", target, fh.err);
        fail(fh.err);
    }
    fh.buffered = buffered;
    fh.libaio = libaio;
    fh.retryIO = retryIO;

    if(posix_memalign((void **)&buf, 512, block_size)) {
        err = errno;
        fprintf(stderr, "error allocating buffer: %d\n", err);
        fail(err);
    }

    if (!readverify) {
        if (libaio) {
            if (init_data_pool(block_size)) goto out;
            if (init_ioreqs()) goto out;
            status = writeaio(&fh, blks);
            err = fh.err;
            free_data_pool();
            free_ioreq_pool();
            if (status) goto out;
        } else {
            status = writenormal(&fh, blks, buf);
            err = fh.err;
            if (status) goto out;
        }

    }

    if (!readverify) fprintf(stderr, ".\n");
    fprintf(stderr, "reading blocks: ");
    fflush(stderr);
    progress = tenp = blks / 10;
    
    fhseek(&fh, 0, SEEK_SET);
    if (fh.err) {
        err = fh.err;
        fprintf(stderr, 
            "error seeking to beginning of file: %d\n", err);
        goto out;
    }

    file_offset = 0;
    for (i = 0; i < blks; i++) {
        do {
            fhread(&fh, buf, block_size, file_offset);
                if (fh.err && retryIO) {
                    struct timespec tm = { .tv_sec = 1,
                                   .tv_nsec = 0 };
                    fprintf(stderr, "error reading blk %llx:"
                         " %d (retrying)\n", i, fh.err);
                    nanosleep(&tm, NULL);
                }
        } while (fh.err && retryIO);

        file_offset += block_size;

        if (fh.err) {
            err = fh.err;
            fprintf(stderr,
                "error reading blk %llx: %d\n", i, fh.err);
        }

        if ((err = check_buf(buf, rand ? blk_table[i] : i)) && !readverify)
            break; //-- if you want only the first bad read back

        if (i == progress) {
            fprintf(stderr, ".");
            fflush(stderr);
            progress += tenp;
        }
    }

 out:
    fprintf(stderr, "\n");
    fflush(stderr);
    
    free(buf);
    fhclose(&fh);

    if (err)
        fail(err);

    free(blk_table);

    fprintf(stdout, "biotest passed\n");
    return 0;
}
