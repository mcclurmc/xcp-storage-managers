import time
import os
import datetime
import subprocess

KiB = 1024
MiB = KiB * KiB
GiB = KiB * MiB
TiB = KiB * GiB

UUID_LEN = 36

RET_RC     = 1
RET_STDERR = 2
RET_STDOUT = 4


class CommandException(Exception):
    pass

class Logger:
    "Log output to a file"

    def __init__(self, fn, verbosity):
        self._logfile = open(fn, 'a')
        self._verbosity = verbosity
        self.log("===== Log started, verbosity=%d, pid=%s =====" % \
                (verbosity, os.getpid()), 1)

    def log(self, msg, verbosity = 1):
        if verbosity > self._verbosity:
            return
        indent = "  " * (verbosity - 1)
        self._logfile.write("%s  %s%s\n" % \
                (datetime.datetime.now(), indent, msg))
        self._logfile.flush()
        if verbosity == 0:
            print msg

    def write(self, msg):
        self._logfile.write("%s\n" % msg)
        self._logfile.flush()

def str2int(strNum):
    num = 0
    if strNum[-1] in ['K', 'k']:
        num = int(strNum[:-1]) * KiB
    elif strNum[-1] in ['M', 'm']:
        num = int(strNum[:-1]) * MiB
    elif strNum[-1] in ['G', 'g']:
        num = int(strNum[:-1]) * GiB
    elif strNum[-1] in ['T', 't']:
        num = int(strNum[:-1]) * TiB
    else:
        num = int(strNum)
    return num
 
def doexec(args, inputtext=None):
        "Execute a subprocess, then return its return code, stdout, stderr"
        proc = subprocess.Popen(args,
                                stdin=subprocess.PIPE,\
                                stdout=subprocess.PIPE,\
                                stderr=subprocess.PIPE,\
                                shell=True,\
                                close_fds=True)
        (stdout,stderr) = proc.communicate(inputtext)
        rc = proc.returncode
        return (rc,stdout,stderr)

def execCmd(cmd, expectedRC, logger, verbosity = 1, ret = None):
    logger.log("`%s`" % cmd, verbosity)
    (rc, stdout, stderr) = doexec(cmd)
    stdoutSnippet = stdout.split('\n')[0]
    if stdoutSnippet != stdout.strip():
        stdoutSnippet += " ..."
    stderrSnippet = stderr.split('\n')[0]
    if stderrSnippet != stderr.strip():
        stderrSnippet += " ..."
    logger.log("(%d), \"%s\" (\"%s\")" % (rc, stdoutSnippet, stderrSnippet), \
            verbosity)
    if type(expectedRC) != type([]):
        expectedRC = [expectedRC]
    if not rc in expectedRC:
        raise CommandException("Command failed: '%s': %d != %s: %s (%s)" % \
                (cmd, rc, expectedRC, stdout.strip(), stderr.strip()))
    if ret == RET_RC:
        return rc
    if ret == RET_STDERR:
        return stderr
    return stdout

def pathExists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False

def validateUUID(uuid):
    if len(uuid) != UUID_LEN:
        print "ha: %d" % len(uuid)
        return False
    if uuid[8] != "-" or uuid[13] != "-" or uuid[18] != "-" or uuid[23] != "-":
        return False
    return True
