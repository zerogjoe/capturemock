
import sys, os, log4py, string, shutil, socket, time, re, stat, locale, subprocess, shlex, types
from ndict import seqdict
from traceback import format_exception
from threading import currentThread
from Queue import Queue, Empty
from copy import copy
from sets import Set
from fnmatch import fnmatch

# We standardise around UNIX paths, it's all much easier that way. They work fine,
# and they don't run into weird issues in being confused with escape characters

# basically posixpath.join, adapted to be portable...
def joinpath(a, *p):
    path = a
    for b in p:
        if os.path.isabs(b) and (path[1:2] != ":" or b[1:2] == ":"):
            path = b
        elif path == '' or path.endswith('/'):
            path +=  b
        else:
            path += '/' + b
    return path

# Copied from posixpath. os.path.expandvars should support this really
_varprog = None
def posixexpandvars(path, getenvFunc=os.getenv):
    """Expand shell variables of form $var and ${var}.  Unknown variables
    are left unchanged."""
    global _varprog
    if '$' not in path:
        return path
    if not _varprog:
        _varprog = re.compile(r'\$(\w+|\{[^}]*\})')
    i = 0
    while True:
        m = _varprog.search(path, i)
        if not m:
            break
        i, j = m.span(0)
        name = m.group(1)
        if name.startswith('{') and name.endswith('}'):
            name = name[1:-1]
        envValue = getenvFunc(name)
        if envValue is not None:
            tail = path[j:]
            path = path[:i] + envValue
            i = len(path)
            path += tail
        else:
            i = j
    return path

# Copied from ntpath
def ntexpandvars(path, getenvFunc=os.getenv): # pragma: no cover
    """Expand shell variables of form $var and ${var}.

    Unknown variables are left unchanged."""
    if '$' not in path:
        return path
    varchars = string.ascii_letters + string.digits + '_-'
    res = ''
    index = 0
    pathlen = len(path)
    while index < pathlen:
        c = path[index]
        if c == '\'':   # no expansion within single quotes
            path = path[index + 1:]
            pathlen = len(path)
            try:
                index = path.index('\'')
                res = res + '\'' + path[:index + 1]
            except ValueError:
                res = res + path
                index = pathlen - 1
        elif c == '$':  # variable or '$$'
            if path[index + 1:index + 2] == '$':
                res = res + c
                index = index + 1
            elif path[index + 1:index + 2] == '{':
                path = path[index+2:]
                pathlen = len(path)
                try:
                    index = path.index('}')
                    var = path[:index]
                    value = getenvFunc(var)
                    if value is not None:
                        res = res + value
                    else:
                        res = res + '${' + var + '}'
                except ValueError:
                    res = res + '${' + path
                    index = pathlen - 1
            else:
                var = ''
                index = index + 1
                c = path[index:index + 1]
                while c != '' and c in varchars:
                    var = var + c
                    index = index + 1
                    c = path[index:index + 1]
                envValue = getenvFunc(var)
                if envValue is not None:
                    res = res + envValue
                else:
                    res = res + '$' + var
                if c != '':
                    index = index - 1
        else:
            res = res + c
        index = index + 1
    return res


os.path.join = joinpath
if os.name == "nt":
    import posixpath
    os.sep = posixpath.sep
    os.path.sep = posixpath.sep
    os.path.normpath = posixpath.normpath
    os.path.expandvars = ntexpandvars
else:
    os.path.expandvars = posixexpandvars

# Useful utility...
def localtime(format= "%d%b%H:%M:%S", seconds=None):
    if not seconds:
        seconds = time.time()
    return time.strftime(format, time.localtime(seconds))

def findInstallationRoots():
    installationRoot = os.path.dirname(os.path.dirname(__file__)).replace("\\", "/")
    if os.path.basename(installationRoot) == "generic":
        siteRoot = os.path.dirname(installationRoot)
        return [ installationRoot, siteRoot ]
    else:
        siteDir = os.path.join(installationRoot, "site")
        if os.path.isdir(siteDir):
            return [ installationRoot, siteDir ]
        else:
            return [ installationRoot ]

globalStartTime = time.time()
installationRoots = findInstallationRoots()

def startTimeString():
    global globalStartTime
    return localtime(seconds=globalStartTime)

def importAndCall(moduleName, callableName, *args):
    command = "from " + moduleName + " import " + callableName + " as _callable"
    exec command
    return _callable(*args)

def installationDir(name):
    # Generic modules only, we're confident we know where they are
    return os.path.join(installationRoots[0], name)

def installationPath(subpath):
    for instRoot in installationRoots:
        instPath = os.path.join(instRoot, subpath)
        if os.path.exists(instPath):
            return instPath

# Parse a time string, either a HH:MM:SS string, or a single int/float,
# which is interpreted as a number of minutes, for backwards compatibility.
# Observe that in either 'field' in the HH:MM:SS case, any number is allowed,
# so e.g. 144:5.3:0.01 is interpreted as 144 hours + 5.3 minutes + 0.01 seconds.
def getNumberOfSeconds(timeString):
    parts = timeString.split(":")
    try:
        if len(parts) == 1:  # Backwards compatible, assume single ints/floats means minutes
            return 60 * float(timeString)
        elif len(parts) <= 3:                # Assume format is HH:MM:SS ...
            seconds = 0
            for i in range(len(parts) - 1, -1, -1):
                if (parts[i] != ""): # Handle empty strings (<=:10 gives empty minutes field, for example)
                    seconds += float(parts[i]) * pow(60, len(parts) - 1 - i)
            return seconds
    except ValueError:
        pass
    raise TextTestError, "Illegal time format '" + timeString + \
          "' :  Use format HH:MM:SS or MM:SS, or a single number to denote a number of minutes."

# Same as above, but gives minutes instead of seconds ...
def getNumberOfMinutes(timeString):
    return getNumberOfSeconds(timeString) / 60

def printWarning(message, stdout = True, stderr = False):
    if stdout:
        print "WARNING: " + message
    if stderr:
        sys.stderr.write("WARNING: " + message + "\n")

# Useful stuff to handle regular expressions
regexChars = re.compile("[\^\$\[\]\{\}\\\*\?\|\+]")
def isRegularExpression(text):
    return (regexChars.search(text) != None)
def findRegularExpression(expr, text):
    try:
        regExpr = re.compile(expr)
        return regExpr.search(text)
    except:
        return False

# Parse the string as a byte expression.
# Mb/mb/megabytes/mbytes
def parseBytes(text): # pragma: no cover
    lcText = text.lower()
    try:
        # Try this first, to save time if it works
        try:
            return float(text)
        except:
            pass

        if lcText.endswith("kb") or lcText.endswith("kbytes") or lcText.endswith("k") or lcText.endswith("kilobytes"):
            splitText = lcText.split('k')
            if len(splitText) > 2:
                raise
            return 1000 * float(splitText[0])
        elif lcText.endswith("kib") or lcText.endswith("kibibytes"):
            splitText = lcText.split('k')
            if len(splitText) > 2:
                raise
            return 1024 * float(splitText[0])
        elif lcText.endswith("mb") or lcText.endswith("mbytes") or lcText.endswith("m") or lcText.endswith("megabytes"):
            splitText = lcText.split('m')
            if len(splitText) > 2:
                raise
            return 1000000 * float(splitText[0])
        elif lcText.endswith("mib") or lcText.endswith("mebibytes"):
            splitText = lcText.split('m')
            if len(splitText) > 2:
                raise
            return 1048576 * float(splitText[0])
        elif lcText.endswith("gb") or lcText.endswith("gbytes") or lcText.endswith("g") or lcText.endswith("gigabytes"):
            splitText = lcText.split('g')
            if len(splitText) > 2:
                raise
            return 1000000000 * float(splitText[0])
        elif lcText.endswith("gib") or lcText.endswith("gibibytes"):
            splitText = lcText.split('g')
            if len(splitText) > 2:
                raise
            return 1073741824 * float(splitText[0])
        elif lcText.endswith("tb") or lcText.endswith("tbytes") or lcText.endswith("t") or lcText.endswith("terabytes"):
            splitText = lcText.split('t')
            if len(splitText) > 3:
                raise
            return 1000000000000 * float(splitText[0])
        elif lcText.endswith("tib") or lcText.endswith("tebibytes"):
            splitText = lcText.split('t')
            if len(splitText) > 3:
                raise
            return 1099511627776 * float(splitText[0])
        elif lcText.endswith("pb") or lcText.endswith("pbytes") or lcText.endswith("p") or lcText.endswith("petabytes"):
            splitText = lcText.split('p')
            if len(splitText) > 2:
                raise
            return 10**15 * float(splitText[0])
        elif lcText.endswith("pib") or lcText.endswith("pebibytes"):
            splitText = lcText.split('p')
            if len(splitText) > 2:
                raise
            return 2**50 * float(splitText[0])
        elif lcText.endswith("eb") or lcText.endswith("ebytes") or lcText.endswith("e") or lcText.endswith("exabytes"):
            splitText = lcText.split('e')
            if len(splitText) > 3:
                raise
            return 10**18 * float(splitText[0])
        elif lcText.endswith("eib") or lcText.endswith("exbibytes"):
            splitText = lcText.split('e')
            if len(splitText) > 3:
                raise
            return 2**60 * float(splitText[0])
        elif lcText.endswith("zb") or lcText.endswith("zbytes") or lcText.endswith("z") or lcText.endswith("zettabytes"):
            splitText = lcText.split('z')
            if len(splitText) > 2:
                raise
            return 10**21 * float(splitText[0])
        elif lcText.endswith("zib") or lcText.endswith("zebibytes"):
            splitText = lcText.split('z')
            if len(splitText) > 2:
                raise
            return 2**70 * float(splitText[0])
        elif lcText.endswith("yb") or lcText.endswith("ybytes") or lcText.endswith("y") or lcText.endswith("yottabytes"):
            splitText = lcText.split('y')
            if len(splitText) > 3:
                raise
            return 10**24 * float(splitText[0])
        elif lcText.endswith("yib") or lcText.endswith("yobibytes"):
            splitText = lcText.split('y')
            if len(splitText) > 2:
                raise
            return 2**80 * float(splitText[0])
        else:
            return float(text)
    except:
        raise "Illegal byte format '" + text + "'"

# pango markup doesn't like <,>,& ...
def convertForMarkup(message):
    return message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# Filter interface: all must provide these three methods
class Filter:
    def acceptsTestCase(self, test):
        return 1
    def acceptsTestSuite(self, suite):
        return 1
    def acceptsTestSuiteContents(self, suite):
        return 1

class TextTriggerGroup:
    def __init__(self, texts):
        self.texts = texts
        self.textTriggers = [ TextTrigger(text) for text in self.texts ]

    def stringContainsText(self, searchString):
        for trigger in self.textTriggers:
            if trigger.matches(searchString):
                return True
        return False

class TextFilter(Filter, TextTriggerGroup):
    def __init__(self, filterText, *args):
        TextTriggerGroup.__init__(self, self.parseInput(filterText, *args))
    def parseInput(self, filterText, *args):
        return commasplit(filterText)
    def containsText(self, test):
        return self.stringContainsText(test.name)
    def acceptsTestSuiteContents(self, suite):
        return not suite.isEmpty()

class ApplicationFilter(TextFilter):
    option = "a"
    def acceptsTestCase(self, test):
        return self.acceptsApplication(test.app)
    def acceptsTestSuite(self, suite):
        return self.acceptsApplication(suite.app)
    def acceptsApplication(self, app):
        return self.stringContainsText(app.name + app.versionSuffix())
    def acceptsTestSuiteContents(self, suite):
        # Allow empty suites through
        return True

class TestPathFilter(TextFilter):
    option = "tp"
    def __init__(self, *args):
        self.diag = getDiagnostics("TestPathFilter")
        TextFilter.__init__(self, *args)
    def parseInput(self, filterText, app, suites):
        allEntries = TextFilter.parseInput(self, filterText, app, suites)
        if allEntries[0].startswith("appdata="):
            # chopped up per application
            return self.parseForApp(allEntries, app, suites)
        else:
            # old style, one size fits all
            return allEntries
    def parseForApp(self, allEntries, app, suites):
        active = False
        myEntries = []
        toFind = self.getSectionsToFind(allEntries, app, suites)
        for entry in allEntries:
            if entry in toFind:
                active = True
            elif entry.startswith("appdata="):
                active = False
            elif active:
                myEntries.append(entry)
        return myEntries
    def getSectionsToFind(self, allEntries, app, suites):        
        allHeaders = filter(lambda entry: entry.startswith("appdata=" + app.name), allEntries)
        if len(allHeaders) == 1:
            return allHeaders[0]
        allApps = filter(lambda a: a.name == app.name, [ suite.app for suite in suites ])
        sections = []
        for header in allHeaders:
            bestApp = self.findAppMatchingSection(header, allApps)
            self.diag.info("Best app for " + header + " = " + repr(bestApp))
            if bestApp is app:
                sections.append(header)

        if len(sections) == 0:
            # We aren't a best-fit for any of them, so we do our best to find one anyway...
            return [ self.findSectionMatchingApp(app, allHeaders) ]
                     
        self.diag.info("Sections for " + repr(app) + " = " + repr(sections))
        return sections

    def findSectionMatchingApp(self, app, allHeaders):
        myVersionSet = Set(app.versions)
        bestVersionSet, bestHeader = None, None
        for header in allHeaders:
            currVersionSet = Set(header.split(".")[1:])
            if bestVersionSet is None or self.isBetterMatch(currVersionSet, bestVersionSet, myVersionSet):
                bestHeader = header
                bestVersionSet = currVersionSet
        return bestHeader

    def findAppMatchingSection(self, header, allApps):
        bestVersionSet, bestApp = None, None
        myVersionSet = Set(header.split(".")[1:])
        self.diag.info("Looking for app matching " + repr(myVersionSet))
        for app in allApps:
            appVersionSet = Set(app.versions)
            if bestVersionSet is None or self.isBetterMatch(appVersionSet, bestVersionSet, myVersionSet):
                bestApp = app
                bestVersionSet = appVersionSet
        return bestApp

    def isBetterMatch(self, curr, best, mine):
        # We want the most in common with mine, and the least not in common
        currCommon = curr.intersection(mine)
        bestCommon = best.intersection(mine)
        if len(currCommon) > len(bestCommon):
            return True
        currDiff = curr.symmetric_difference(mine)
        bestDiff = best.symmetric_difference(mine)
        return len(currDiff) < len(bestDiff)

    def acceptsTestCase(self, test):
        return test.getRelPath() in self.texts
    def acceptsTestSuite(self, suite):
        for relPath in self.texts:
            if relPath.startswith(suite.getRelPath()):
                return True
        return False

# Generic action to be performed: all actions need to provide these methods
class Action:
    def __call__(self, test):
        pass
    def setUpSuite(self, suite):
        pass
    def setUpApplication(self, app):
        pass
    def tearDownSuite(self, suite):
        pass
    def kill(self, test, sig):
        pass
    def callDuringAbandon(self, test):
        # set to True if tests should have this action called even after all is reckoned complete (e.g. UNRUNNABLE)
        return False
    # Useful for printing in a certain format...
    def describe(self, testObj, postText = ""):
        print testObj.getIndent() + repr(self) + " " + repr(testObj) + postText
    def __repr__(self):
        return "Doing nothing on"
    def __str__(self):
        return str(self.__class__)

class ScriptWithArgs(Action):
    def parseArguments(self, args):
        currKey = ""
        dict = {}
        for arg in args:
            if arg.find("=") != -1:
                currKey, val = arg.split("=")
                dict[currKey] = val
            elif dict.has_key(currKey):
                dict[currKey] += " " + arg
        return dict

def addCategory(name, briefDesc, longDesc = ""):
    if longDesc:
        TestState.categoryDescriptions[name] = briefDesc, longDesc
    else:
        TestState.categoryDescriptions[name] = briefDesc, briefDesc

# Observer mechanism shouldn't allow for conflicting notifications. Use main thread at all times
class ThreadedNotificationHandler:
    def __init__(self):
        self.workQueue = Queue()
        self.active = False
    def enablePoll(self, idleHandleMethod):
        self.active = True
        return idleHandleMethod(self.pollQueue)
    def disablePoll(self):
        self.active = False
    def pollQueue(self):
        try:
            observable, args, kwargs = self.workQueue.get_nowait()
            observable.diagnoseObs("From work queue", *args, **kwargs)
            observable.performNotify(*args, **kwargs)
        except Empty:
            # Idle handler. We must sleep for a bit if we don't do anything, or we use the whole CPU (busy-wait)
            time.sleep(0.1)
        return self.active
    def transfer(self, observable, *args, **kwargs):
        self.workQueue.put((observable, args, kwargs))

class Observable:
    threadedNotificationHandler = ThreadedNotificationHandler()
    obsDiag = None
    @classmethod
    def diagnoseObs(klass, message, *args, **kwargs):
        if not klass.obsDiag:
            klass.obsDiag = getDiagnostics("Observable")
        klass.obsDiag.info(message + " " + str(klass) + " " + repr(args) + repr(kwargs))

    def __init__(self, passSelf=False):
        self.observers = []
        self.passSelf = passSelf

    def addObserver(self, observer):
        self.observers.append(observer)

    def setObservers(self, observers):
        self.observers = filter(lambda x: x is not self, observers)

    def inMainThread(self):
        return currentThread().getName() == "MainThread"

    def notify(self, *args, **kwargs):
        if self.threadedNotificationHandler.active and not self.inMainThread():
            self.diagnoseObs("To work queue", *args, **kwargs)
            self.threadedNotificationHandler.transfer(self, *args, **kwargs)
        else:
            self.diagnoseObs("Perform directly", *args, **kwargs)
            self.performNotify(*args, **kwargs)

    def notifyThreaded(self, *args, **kwargs):
        # join the idle handler queue even if we're the main thread
        if self.threadedNotificationHandler.active:
            self.diagnoseObs("To work queue explicitly", *args, **kwargs)
            self.threadedNotificationHandler.transfer(self, *args, **kwargs)
        else:
            self.diagnoseObs("Perform directly", *args, **kwargs)
            self.performNotify(*args, **kwargs)

    def notifyIfMainThread(self, *args, **kwargs):
        if not self.inMainThread():
            return
        else:
            self.diagnoseObs("Perform directly", *args, **kwargs)
            self.performNotify(*args, **kwargs)

    def performNotify(self, name, *args, **kwargs):
        methodName = "notify" + name
        for observer in self.observers:
            if hasattr(observer, methodName):
                retValue = self.notifyObserver(observer, methodName, *args, **kwargs)
                if retValue is not None: # break off the chain if we get a non-None value back
                    break

    def notifyObserver(self, observer, methodName, *args, **kwargs):
        # doesn't matter if only some of the observers have the method
        method = eval("observer." + methodName)
        # unpickled objects have not called __init__, and
        # hence do not have self.passSelf ...
        if hasattr(self, "passSelf") and self.passSelf:
            return method(self, *args, **kwargs)
        else:
            return method(*args, **kwargs)

# Generic state which tests can be in, should be overridden by subclasses
# Acts as a static state for tests which have not run (yet)
# Free text is text of arbitrary length: it will appear in the "Text Info" GUI window when the test is viewed
# and the "details" section in batch mode
# Brief text should be at most two or three words: it appears in the details column in the main GUI window and in
# the summary of batch mode
class TestState(Observable):
    categoryDescriptions = seqdict()
    showExecHosts = 0
    def __init__(self, category, freeText = "", briefText = "", started = 0, completed = 0,\
                 executionHosts = [], lifecycleChange = ""):
        Observable.__init__(self)
        self.category = category
        self.freeText = freeText
        self.briefText = briefText
        self.started = started
        self.completed = completed
        self.executionHosts = executionHosts
        self.lifecycleChange = lifecycleChange
    def __str__(self):
        return self.freeText
    def __repr__(self):
        return self.categoryRepr() + self.hostRepr() + self.colonRepr()
    def categoryRepr(self):
        if not self.categoryDescriptions.has_key(self.category):
            return self.category
        briefDescription, longDescription = self.categoryDescriptions[self.category]
        return longDescription
    def hostString(self):
        if len(self.executionHosts) == 0:
            return "(no execution hosts given)"
        else:
            return "on " + string.join(self.executionHosts, ",")
    def hostRepr(self):
        if self.showExecHosts and len(self.executionHosts) > 0:
            return " " + self.hostString()
        else:
            return ""
    def colonRepr(self):
        if self.hasSucceeded():
            return ""
        else:
            return " :"
    def getComparisonsForRecalculation(self):
        # Is some aspect of the state out of date
        return []
    # Used by text interface to print states
    def description(self):
        if self.freeText:
            if self.freeText.find("\n") == -1:
                return "not compared:  " + self.freeText
            else:
                return "not compared:\n" + self.freeText
        else:
            return "not compared"
    def getFreeText(self):
        return self.freeText # some subclasses might want to calculate this...
    def getTypeBreakdown(self):
        if self.isComplete():
            return "failure", self.briefText
        else:
            return self.category, self.briefText
    def hasStarted(self):
        return self.started or self.completed
    def isComplete(self):
        return self.completed
    def hasSucceeded(self):
        return 0
    def hasFailed(self):
        return self.isComplete() and not self.hasSucceeded()
    def isMarked(self):
        return False
    def hasResults(self):
        # Do we have actual results that can be compared
        return 0
    def shouldAbandon(self):
        return self.lifecycleChange == "complete"
    def isSaveable(self):
        return self.hasFailed() and self.hasResults()
    def warnOnSave(self): #pragma : no cover - only called on saveable tests usually
        return False
    def updateAbsPath(self, newAbsPath):
        pass
    def updateTmpPath(self, newTmpPath):
        pass

addCategory("unrunnable", "unrunnable", "could not be run")
addCategory("marked", "marked", "was marked by the user")

class Unrunnable(TestState):
    def __init__(self, freeText, briefText, executionHosts=[], lifecycleChange=""):
        TestState.__init__(self, "unrunnable", freeText, briefText, completed=1, \
                           executionHosts=executionHosts, lifecycleChange=lifecycleChange)
    def shouldAbandon(self):
        return True

class MarkedTestState(TestState):
    def __init__(self, freeText, briefText, oldState, executionHosts=[]):
        fullText = freeText + "\n\nORIGINAL STATE:\nTest " + repr(oldState) + "\n " + oldState.freeText
        TestState.__init__(self, "marked", fullText, briefText, completed=1, \
                           executionHosts=executionHosts, lifecycleChange="marked")
        self.oldState = oldState
    # We must implement this ourselves, since we want to be neither successful nor
    # failed, and by default hasFailed is implemented as 'not hasSucceeded()'.
    def hasFailed(self):
        return False
    def isMarked(self):
        return True
    def getTypeBreakdown(self):
        return self.category, self.briefText

def configureLog4py(configFile):
    # Don't use the default locations, particularly current directory causes trouble
    if len(log4py.CONFIGURATION_FILES) > 1:
        del log4py.CONFIGURATION_FILES[1]
    if configFile:
        # To set new config files appears to require a constructor...
        rootLogger = log4py.Logger(log4py.TRUE, configFile)
    else:
        rootLogger = log4py.Logger().get_root()
        rootLogger.set_loglevel(log4py.LOGLEVEL_NONE)

# Simple handle to get diagnostics object. Better than using log4py directly,
# as it ensures everything appears by default in a standard place with a standard name.
def getDiagnostics(diagName):
    return log4py.Logger().get_instance(diagName)

def getPersonalConfigDir():
    fromEnv = os.getenv("TEXTTEST_PERSONAL_CONFIG")
    if fromEnv:
        return fromEnv
    else:
        return os.path.normpath(os.path.expanduser("~/.texttest"))

# Return the hostname, guaranteed to be just the hostname...
def gethostname():
    fullname = socket.gethostname()
    return fullname.split(".")[0]

# Hacking around os.path.getcwd not working with AMD automounter
def abspath(relpath):
    if os.environ.has_key("PWD"):
        return os.path.join(os.environ["PWD"], relpath)
    else:
        return os.path.abspath(relpath)

# deepcopy(os.environ) still seems to retain links to the actual environment, create a clean copy
def copyEnvironment():
    environ = {}
    for var, value in os.environ.items():
        environ[var] = value
    return environ

def getInterpreter(executable):
    if executable.endswith(".py"):
        return "python"
    elif executable.endswith(".rb"):
        return "ruby"
    elif executable.endswith(".jar"):
        return "java -jar"
    else:
        return ""

def commandLineString(cmdArgs):
    def getQuoteChar(char):
        if char == "\"" and os.name == "posix":
            return "'"
        else:
            return '"'

    def quote(arg):
        quoteChars = "'\"|* "
        for char in quoteChars:
            if char in arg:
                quoteChar = getQuoteChar(char)
                return quoteChar + arg + quoteChar
        return arg.replace("\\", "/")

    return " ".join(map(quote, cmdArgs))

def relpath(fullpath, parentdir):
    normFull = os.path.normpath(fullpath)
    relPath = normFull.replace(os.path.normpath(parentdir), "")
    if relPath == normFull:
        # unrelated
        return None
    if relPath.startswith(os.sep):
        return relPath[1:]
    else:
        return relPath

def getProcessStartUpInfo(getenvFunc=os.getenv):
    # Used for hiding the windows if we're on Windows!
    if os.name == "nt" and getenvFunc("DISPLAY") == "HIDE_WINDOWS":
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
        return info

# Useful utility, free text input as comma-separated list which may have spaces
def commasplit(input):
    return map(string.strip, input.split(","))

# Another useful thing that saves an import and remembering weird stuff
def modifiedTime(filename):
    try:
        return os.stat(filename)[stat.ST_MTIME]
    except OSError:
        # Dead links etc.
        return None

# portable version of shlex.split
# See http://sourceforge.net/tracker/index.php?func=detail&aid=1724366&group_id=5470&atid=105470
# As usual, UNIX paths work better on Windows than Windows paths...
def splitcmd(s):
    if os.name == "posix":
        return shlex.split(s)
    else:
        return shlex.split(s.replace("\\", "\\\\"))

# portable version of os.path.samefile
def samefile(writeDir, currDir):
    try:
        return os.path.samefile(writeDir, currDir)
    except:
        # samefile doesn't exist on Windows, but nor do soft links so we can
        # do a simpler version
        return os.path.normpath(writeDir.replace("\\", "/")) == os.path.normpath(currDir.replace("\\", "/"))

# Version of rmtree not prone to crashing if directory in use or externally removed
def rmtree(dir, attempts=100):
    realDir = os.path.realpath(dir)
    if not os.path.isdir(realDir):
        print "Write directory", dir, "externally removed"
        return
    # Don't be somewhere under the directory when it's removed
    try:
        if os.getcwd().startswith(realDir):
            root, local = os.path.split(dir)
            os.chdir(root)
    except OSError:
        pass
    for i in range(attempts):
        try:
            shutil.rmtree(realDir)
            return
        except OSError:
            if os.path.isdir(realDir):
                if i == attempts - 1:
                    print "Unable to remove directory", dir, ":"
                    printException()
                else:
                    print "Problems removing directory", dir, "- waiting 1 second to retry..."
                    time.sleep(1)

def readList(filename):
    try:
        items = []
        for longline in open(filename).readlines():
            line = longline.strip()
            if len(line) > 0 and not line.startswith("#"):
                items.append(line)
        return items
    except IOError:
        return [] # It could be a broken link: don't bail out if so...

emptyLineSymbol = "__EMPTYLINE__"

def readListWithComments(filename, filterMethod=None):
    items = seqdict()
    currComment = ""
    for longline in open(filename).readlines():
        line = longline.strip()
        if len(line) == 0:
            if currComment:
                currComment += emptyLineSymbol + "\n"
            continue
        if line.startswith("#"):
            currComment += longline[1:].lstrip()
        else:
            if filterMethod:
                failReason = filterMethod(line, items, filename)
                if failReason:
                    currComment += line + " (automatically commented due to " + failReason + ")\n" + emptyLineSymbol + "\n"
                    continue
            items[line] = currComment.strip()
            currComment = ""
    # Rescue dangling comments in the end (but put them before last test ...)
    if currComment and len(items) > 0:
        items[items.keys()[-1]] = currComment + items[items.keys()[-1]]
    return items

# comment can contain lines with __EMPTYLINE__ (see above) as a separator
# from free comments, or commented out tests. This method extracts the comment
# after any __EMPTYLINE__s.
def extractComment(comment):
    lastEmptyLinePos = comment.rfind(emptyLineSymbol)
    if lastEmptyLinePos == -1:
        return comment
    else:
        return comment[lastEmptyLinePos + len(emptyLineSymbol) + 1:]

# comment can contain lines with __EMPTYLINE__ (see above) as a separator
# from free comments, or commented out tests. This method replaces the real
# comment for this test with newComment
def replaceComment(comment, newComment):
    lastEmptyLinePos = comment.rfind(emptyLineSymbol)
    if lastEmptyLinePos == -1:
        return newComment
    else:
        return comment[0:lastEmptyLinePos + len(emptyLineSymbol) + 1] + newComment

def openForWrite(path):
    ensureDirExistsForFile(path)
    return open(path, "w")

# Make sure the dir exists
def ensureDirExistsForFile(path):
    dir, localName = os.path.split(path)
    ensureDirectoryExists(dir)

def addLocalPrefix(fullPath, prefix):
    dir, file = os.path.split(fullPath)
    return os.path.join(dir, prefix + "_" + file)

def ensureDirectoryExists(path, attempts=5):
    # os.makedirs seems to be a bit flaky, especially if the file server is loaded
    # or on silly platforms like powerpc. We give it five chances to do its stuff :)
    for attempt in range(attempts):
        if os.path.isdir(path):
            return
        try:
            os.makedirs(path)
        except OSError, detail:
            if attempt == attempts - 1:
                raise

def retryOnInterrupt(function, *args):
    try:
        return function(*args)
    except (IOError, OSError), detail:
        if str(detail).find("Interrupted system call") != -1:
            return retryOnInterrupt(function, *args)
        else:
            raise

def getExceptionString():
    type, value, traceback = sys.exc_info()
    return "".join(format_exception(type, value, traceback))

def printException():
    sys.stderr.write("Description of exception thrown :\n")
    exceptionString = getExceptionString()
    sys.stderr.write(exceptionString)
    return exceptionString

class PreviewGenerator:
    def __init__(self, maxWidth, maxLength, startEndRatio=1):
        self.maxWidth = maxWidth
        self.cutFromStart = int(maxLength * startEndRatio)
        self.cutFromEnd = maxLength - self.cutFromStart
    def getCutLines(self, lines):
        if len(lines) < self.cutFromEnd + self.cutFromStart:
            return lines

        cutLines = lines[:self.cutFromStart]
        if self.cutFromEnd > 0:
            cutLines.append("... extra data truncated by TextTest ...\n")
            cutLines += lines[-self.cutFromEnd:]
        return cutLines
    def getPreview(self, file):
        fileLines = retryOnInterrupt(self.getFileLines, file)
        return self._getPreview(fileLines)
    def getFileLines(self, file):
        lines = file.readlines()
        file.close()
        return lines
    def _getPreview(self, lines):
        cutLines = self.getCutLines(lines)
        lines = map(self.getWrappedLine, cutLines)
        return string.join(lines, "")
    def getPreviewFromText(self, text):
        truncatedLines = text.splitlines()
        lines = [ line + "\n" for line in truncatedLines ]
        return self._getPreview(lines)
    def getWrappedLine(self, line):
        remaining = line
        result = ""
        while True:
            if len(remaining) <= self.maxWidth:
                return result + remaining
            result += remaining[:self.maxWidth] + "\n"
            remaining = remaining[self.maxWidth:]

# Exception to throw. It's generally good to throw this internally
class TextTestError(RuntimeError):
    pass

class TextTestWarning(RuntimeError):
    pass

# Sort of a workaround to get e.g. CVSLogInGUI to show a message in a simple info dialog
class TextTestInformation(RuntimeError):
    pass

# Yes, we know that getopt exists. However it throws exceptions when it finds unrecognised things, and we can't do that...
class OptionFinder(seqdict):
    def __init__(self, args, defaultKey = "default"):
        seqdict.__init__(self)
        self.buildOptions(args, defaultKey)
    def buildOptions(self, args, defaultKey):
        optionKey = None
        for item in args:
            if item.startswith("-"):
                optionKey = self.stripMinuses(item)
                self[optionKey] = None
            elif optionKey:
                if self[optionKey] is not None:
                    self[optionKey] += " "
                else:
                    self[optionKey] = ""
                self[optionKey] += item.strip()
            else:
                self[defaultKey] = item
    def stripMinuses(self, item):
        if len(item) > 1 and item[1] == "-":
            return item[2:].strip()
        else:
            return item[1:].strip()

class TextTrigger:
    def __init__(self, text):
        self.text = text
        self.regex = None
        if isRegularExpression(text):
            try:
                self.regex = re.compile(text)
            except:
                pass
    def __repr__(self):
        return self.text
    def matches(self, line, lineNumber=0):
        if self.regex:
            return self.regex.search(line)
        else:
            return line.find(self.text) != -1
    def replace(self, line, newText):
        if self.regex:
            return re.sub(self.text, newText, line)
        else:
            return line.replace(self.text, newText)

# Used for application and personal configuration files
class MultiEntryDictionary(seqdict):
    def __init__(self, importKey="", importFileFinder=None):
        seqdict.__init__(self)
        self.currDict = self
        self.diag = getDiagnostics("MultiEntryDictionary")
        self.aliases = {}
        self.importKey = importKey
        self.importFileFinder= importFileFinder
        
    def setAlias(self, aliasName, realName):
        self.aliases[aliasName] = realName

    def getEntryName(self, fromConfig):
        return self.aliases.get(fromConfig, fromConfig)

    def readValues(self, fileNames, *args, **kwargs):
        self.currDict = self
        for filename in fileNames:
            self.readFromFile(filename, *args, **kwargs)

    def readFromFile(self, filename, *args, **kwargs):
        self.diag.info("Reading file " + filename)
        for line in readList(filename):
            self.parseConfigLine(line, *args, **kwargs)
        self.currDict = self

    def parseConfigLine(self, line, insert=True, errorOnUnknown=False):
        if line.startswith("[") and line.endswith("]"):
            sectionName = self.getEntryName(line[1:-1])
            self.currDict = self.changeSectionMarker(sectionName, errorOnUnknown)
        elif line.find(":") != -1:
            key, value = line.split(":", 1)
            entryName = self.getEntryName(os.path.expandvars(key))
            self.addEntry(entryName, value, "", insert, errorOnUnknown)
            if key and key == self.importKey:
                self.readFromFile(self.importFileFinder(os.path.expandvars(value)), insert, errorOnUnknown)
        else:
            printWarning("Could not parse config line " + line, stdout = False, stderr = True)
            
    def changeSectionMarker(self, name, errorOnUnknown):
        if name == "end":
            return self
        if self.has_key(name) and type(self[name]) == types.DictType:
            return self[name]
        if errorOnUnknown:
            printWarning("Config section name '" + name + "' not recognised.", stdout = False, stderr = True)
        return self

    def addEntry(self, entryName, entry, sectionName="", insert=0, errorOnUnknown=1):
        if sectionName:
            self.currDict = self[sectionName]
        entryExists = self.currDict.has_key(entryName)
        if entryExists:
            self.diag.info("Entry existed, setting " + entryName + "=" + entry)
            self.insertEntry(entryName, entry)
        else:
            if insert or not self.currDict is self:
                self.diag.info("Inserting " + entryName + "=" + repr(entry))
                self.currDict[entryName] = self.castEntry(entry)
            elif errorOnUnknown:
                printWarning("Config entry name '" + entryName + "' not recognised.", stdout = False, stderr = True)
        # Make sure we reset...
        if sectionName:
            self.currDict = self
    def getDictionaryValueType(self):
        val = self.currDict.values()
        if len(val) == 0:
            return types.StringType
        else:
            return type(val[0])

    def castEntry(self, entry):
        if type(entry) != types.StringType:
            return entry
        dictValType = self.getDictionaryValueType()
        if dictValType == types.ListType:
            return self.getListValue(entry)
        else:
            return dictValType(entry)

    def getListValue(self, entry, currentList=[]):
        if entry == "{CLEAR LIST}":
            return []
        elif entry not in currentList:
            return currentList + [ entry ]
        else:
            return currentList

    def insertEntry(self, entryName, entry):
        currType = type(self.currDict[entryName])
        if currType == types.ListType:
            self.currDict[entryName] = self.getListValue(entry, self.currDict[entryName])
        elif currType == types.DictType:
            self.currDict = self.currDict[entryName]
            self.insertEntry("default", entry)
            self.currDict = self
        else:
            self.currDict[entryName] = currType(entry)

    def getComposite(self, key, subKey, defaultSubKey="default"):
        dict = self.get(key)
        # If it wasn't a dictionary, return None
        if not hasattr(dict, "items"):
            return None
        listVal = []
        for currSubKey, currValue in dict.items():
            if fnmatch(subKey, currSubKey):
                if type(currValue) == types.ListType:
                    listVal += currValue
                else:
                    return currValue
        # A certain amount of duplication here - hard to see how to avoid it
        # without compromising performance though...
        defValue = dict.get(defaultSubKey)
        if defValue is not None:
            if type(defValue) == types.ListType:
                listVal += defValue
                return listVal
            else:
                return defValue
        else:
            if len(listVal) > 0:
                return listVal


class Option:
    def __init__(self, name, value, description, changeMethod):
        self.name = name
        self.defaultValue = value
        self.valueMethod = None
        self.updateMethod = None
        self.description = description
        self.changeMethod = changeMethod
    def getValue(self):
        if self.valueMethod:
            return self.valueMethod()
        else:
            return self.defaultValue
    def setValue(self, value):
        self.defaultValue = value
        if self.updateMethod:
            self.updateMethod(value)
    def setMethods(self, valueMethod, updateMethod):
        self.valueMethod = valueMethod
        self.updateMethod = updateMethod
    def reset(self):
        if self.updateMethod:
            self.updateMethod(self.defaultValue)
        else:
            self.valueMethod = None

class TextOption(Option):
    def __init__(self, name, value="", possibleValues=[], allocateNofValues=-1,
                 selectDir=False, selectFile=False, saveFile=False, possibleDirs=[], description="", changeMethod = None):
        Option.__init__(self, name, value, description, changeMethod)
        self.possValAppendMethod = None
        self.possValListMethod = None
        self.nofValues = allocateNofValues
        self.selectDir = selectDir
        self.selectFile = selectFile
        self.saveFile = saveFile
        self.possibleDirs = possibleDirs
        self.clearMethod = None
        self.setPossibleValues(possibleValues)
    def setPossibleValuesMethods(self, appendMethod, getMethod):
        self.possValListMethod = getMethod
        if appendMethod:
            self.possValAppendMethod = appendMethod
            self.updatePossibleValues()
    def updatePossibleValues(self):
        if self.possValAppendMethod:
            for value in self.possibleValues:
                self.possValAppendMethod(value)
    def listPossibleValues(self):
        if self.possValListMethod:
            return self.possValListMethod()
        else:
            return self.possibleValues
    def addPossibleValue(self, value):
        if value not in self.possibleValues:
            self.possibleValues.append(value)
            if self.possValAppendMethod:
                self.possValAppendMethod(value)
            return True
        else:
            return False
    def setValue(self, value):
        Option.setValue(self, value)
        if self.usePossibleValues():
            self.setPossibleValues(self.possibleValues)
    def setPossibleValues(self, values):
        if self.defaultValue in values:
            self.possibleValues = values
        else:
            self.possibleValues = [ self.defaultValue ] + values
        self.clear()
        self.updatePossibleValues()
    def getPossibleDirs(self):
        if self.selectDir:
            return self.possibleValues
        else:
            return self.possibleDirs

    def usePossibleValues(self):
        return self.selectDir or self.nofValues > 1 or len(self.possibleValues) > 1
    def setClearMethod(self, clearMethod):
        self.clearMethod = clearMethod
    def clear(self):
        if self.clearMethod:
            self.clearMethod()
    def getValue(self):
        basic = Option.getValue(self)
        if (self.selectFile or self.saveFile or self.selectDir) and basic:
            # If files are returned, turn the paths into UNIX format...
            return basic.replace("\\", "/")
        else:
            return basic

    def getDirectories(self):
        allDirs = self.getPossibleDirs()
        for dir in allDirs:
            try:
                ensureDirectoryExists(dir)
            except OSError: # Might not have permissions
                allDirs.remove(dir)
                
        return allDirs, self.findDefaultDirectory(allDirs)

    def findDefaultDirectory(self, allDirs):
        # Set first non-empty dir as default ...)
        for dir in allDirs:
            if self.saveFile or len(os.listdir(dir)) > 0:
                return dir

        return allDirs[0]

class Switch(Option):
    def __init__(self, name="", value=0, options=[], description="", changeMethod = None):
        Option.__init__(self, name, int(value), description, changeMethod)
        self.options = options
        self.resetMethod = None
    def setValue(self, value):
        Option.setValue(self, int(value))
    def reset(self):
        if self.defaultValue == 0 and self.resetMethod:
            self.resetMethod(1)
        else:
            Option.reset(self)
    def describe(self):
        text = self.name
        if len(self.options) > 0:
            text += self.options[-1]
        return text

class OptionGroup:
    def __init__(self, name):
        self.name = name
        self.options = seqdict()
        self.switches = seqdict()
    def reset(self):
        for option in self.options.values():
            option.reset()
        for switch in self.switches.values():
            switch.reset()
    def setValue(self, key, value):
        if self.options.has_key(key):
            self.options[key].setValue(value)
            return 1
        elif self.switches.has_key(key):
            self.switches[key].setValue(value)
            return 1
        return 0 #pragma : no cover - should never happen
    def addSwitch(self, key, *args, **kwargs):
        if self.switches.has_key(key):
            return False
        self.switches[key] = Switch(*args, **kwargs)
        return True
    def addOption(self, key, *args, **kwargs):
        if self.options.has_key(key):
            return False
        self.options[key] = TextOption(*args, **kwargs)
        return True
    def getSwitchValue(self, key, defValue = None):
        if self.switches.has_key(key):
            return self.switches[key].getValue()
        else:
            return defValue
    def getOptionValue(self, key, defValue = None):
        if self.options.has_key(key):
            return self.options[key].getValue()
        else:
            return defValue
    def setPossibleValues(self, key, possibleValues):
        option = self.options.get(key)
        if option:
            option.setPossibleValues(possibleValues)
    def getOption(self, key):
        return self.options.get(key)
    def setOptionValue(self, key, value):
        if self.options.has_key(key):
            return self.options[key].setValue(value)
    def getOptionValueMap(self):
        values = {}
        for key, option in self.options.items():
            value = option.getValue()
            if len(value):
                values[key] = option.getValue()
        return values
    def keys(self):
        return self.options.keys() + self.switches.keys()
    def getCommandLines(self, onlyKeys=[]):
        commandLines = []
        for key, option in self.options.items():
            if self.accept(key, option, onlyKeys):
                commandLines.append("-" + key)
                commandLines.append(option.getValue())
        for key, switch in self.switches.items():
            if self.accept(key, switch, onlyKeys):
                commandLines.append("-" + key)
        return commandLines
    def accept(self, key, option, onlyKeys):
        if not option.getValue():
            return False
        if len(onlyKeys) == 0:
            return True
        return key in onlyKeys
    def readCommandLineArguments(self, args):
        for arg in args:
            if arg.find("=") != -1:
                option, value = arg.split("=")
                if self.options.has_key(option):
                    self.options[option].defaultValue = value
                else:
                    raise TextTestError, self.name + " does not support option '" + option + "'"
            else:
                if self.switches.has_key(arg):
                    oldValue = self.switches[arg].defaultValue
                    # Toggle the value from the default
                    self.switches[arg].defaultValue = 1 - oldValue
                else:
                    raise TextTestError, self.name + " does not support switch '" + arg + "'"

def decodeText(text, log = None):
    localeEncoding = locale.getdefaultlocale()[1]
    if localeEncoding:
        try:
            return unicode(text, localeEncoding, errors="strict")
        except:
            if log:
                log.info("WARNING: Failed to decode string '" + text + \
                         "' using default locale encoding " + repr(localeEncoding) + \
                         ". Trying ISO8859-1 encoding ...")

    return decodeISO88591Text(text, localeEncoding, log)

def decodeISO88591Text(text, localeEncoding, log = None):
    try:
        return unicode(text, 'ISO8859-1', errors="strict")
    except:
        if log:
            log.info("WARNING: Failed to decode string '" + text + \
                     "' using ISO8859-1 encoding " + repr(localeEncoding) + \
                     ". Trying strict UTF-8 encoding ...")

        return decodeUtf8Text(text, localeEncoding, log)

def decodeUtf8Text(text, localeEncoding, log = None):
    try:
        return unicode(text, 'utf-8', errors="strict")
    except:
        if log:
            log.info("WARNING: Failed to decode string '" + text + \
                     "' both using strict UTF-8 and " + repr(localeEncoding) + \
                     " encodings.\nReverting to non-strict UTF-8 encoding but " + \
                     "replacing problematic\ncharacters with the Unicode replacement character, U+FFFD.")
        return unicode(text, 'utf-8', errors="replace")

def encodeToUTF(unicodeInfo, log = None):
    try:
        return unicodeInfo.encode('utf-8', 'strict')
    except:
        try:
            if log:
                log.info("WARNING: Failed to encode Unicode string '" + unicodeInfo + \
                         "' using strict UTF-8 encoding.\nReverting to non-strict UTF-8 " + \
                         "encoding but replacing problematic\ncharacters with the Unicode replacement character, U+FFFD.")
            return unicodeInfo.encode('utf-8', 'replace')
        except:
            if log:
                log.info("WARNING: Failed to encode Unicode string '" + unicodeInfo + \
                         "' using both strict UTF-8 encoding and UTF-8 encoding with " + \
                         "replacement. Showing error message instead.")
            return "Failed to encode Unicode string."

def encodeToLocale(unicodeInfo, log = None):
    localeEncoding = locale.getdefaultlocale()[1]
    if localeEncoding:
        try:
            return unicodeInfo.encode(localeEncoding, 'strict')
        except:
            if log:
                log.info("WARNING: Failed to encode Unicode string '" + unicodeInfo + \
                         "' using strict '" + localeEncoding + "' encoding.\nReverting to non-strict UTF-8 " + \
                         "encoding but replacing problematic\ncharacters with the Unicode replacement character, U+FFFD.")
    return unicodeInfo.encode('utf-8', 'replace')

def rel_pathsplit(p, rest=[]):
    (h,t) = os.path.split(p)
    if len(h) < 1: return [t]+rest
    if len(t) < 1: return [h]+rest
    return rel_pathsplit(h,[t]+rest)

def commonpath(l1, l2, common=[]):
    if len(l1) < 1: return (common, l1, l2)
    if len(l2) < 1: return (common, l1, l2)
    if l1[0] != l2[0]: return (common, l1, l2)
    return commonpath(l1[1:], l2[1:], common+[l1[0]])

def relativepath(p1, p2):
    (common,l1,l2) = commonpath(rel_pathsplit(p1), rel_pathsplit(p2))
    p = []
    if len(l1) > 0:
        p = [ '../' * len(l1) ]
    p = p + l2
    return os.path.join( *p )

# pwd and grp doesn't exist on windows ...
import stat
try:
    import pwd, grp
except:
    pass

class FileProperties:
    def __init__(self, path):
        self.abspath = path
        self.filename = os.path.basename(self.abspath)
        self.dir = os.path.dirname(self.abspath)
        self.status = os.stat(self.abspath)
        self.now = int(time.time())
        self.recent = self.now - (6 * 30 * 24 * 60 * 60) #6 months ago
    def inqType(self):
        # The stat.S_IS* functions don't seem to work on links ...
        if os.path.islink(self.abspath):
            return "l"
        elif os.path.isdir(self.abspath):
            return "d"
        else:
            return "-"
    def inqMode(self):
        permissions = ""
        for who in "USR", "GRP", "OTH":
            for what in "R", "W", "X":
               #lookup attribute at runtime using getattr
               if self.status[stat.ST_MODE] & getattr(stat,"S_I" + what + who):
                   permissions = permissions + what.lower()
               else:
                   permissions = permissions + "-"
        return permissions
    def inqLinks(self):
        return self.status[stat.ST_NLINK]
    def inqOwner(self):
        try:
            uid = self.status[stat.ST_UID]
            return str(pwd.getpwuid(uid)[0])
        except:
            return "?"
    def inqGroup(self):
        try:
            gid = self.status[stat.ST_GID]
            return str(grp.getgrgid(gid)[0])
        except:
            return "?"
    def inqSize(self):
        return self.status[stat.ST_SIZE]
    def formatTime(self, timeStamp):
        # %e is more appropriate than %d below, as it fills with space
        # rather than 0, but it is not supported on Windows, it seems.
        if timeStamp < self.recent or \
               timeStamp > self.now:
            timeFormat = "%b %d  %Y"
        else:
            timeFormat = "%b %d %H:%M"
        return time.strftime(timeFormat, time.localtime(timeStamp))
    def inqModificationTime(self):
        return self.formatTime(self.status[stat.ST_MTIME])
    # Return the *nix type format:
    # -rwxr--r--    1 mattias carm       1675 Nov 16  1998 .xinitrc_old
    def getUnixRepresentation(self):
        return (self.inqType(), self.inqMode(),
                self.inqLinks(), self.inqOwner(),
                self.inqGroup(), self.inqSize(),
                self.inqModificationTime(), self.filename)
    def getUnixStringRepresentation(self):
        return "%s%s %3d %-8s %-8s %8d %s %s" % self.getUnixRepresentation()
    def getDescription(self):
        return "Showing properties of the file " + self.abspath + ":\n" + self.getUnixStringRepresentation()
