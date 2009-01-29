#!/usr/bin/env python

import os, sys
from tempfile import mktemp

def interpretCore(corefile):
    if os.path.getsize(corefile) == 0:
        details = "Core file of zero size written - Stack trace not produced for crash\nCheck your coredumpsize limit"
        return "Empty core file", details, None
    
    binary = getBinary(corefile)
    if not os.path.isfile(binary):
        details = "Could not find binary name '" + binary + "' from core file : Stack trace not produced for crash"
        return "No binary found from core", details, None

    summary, details = writeGdbStackTrace(corefile, binary)
    if summary.find("Parse failure") != -1:
        dbxSummary, dbxDetails = writeDbxStackTrace(corefile, binary)
        if dbxSummary.find("Parse failure") == -1:
            return dbxSummary, dbxDetails, binary
        else:
            return "Parse failure from both GDB and DBX", details + dbxDetails, binary
    else:
        return summary, details, binary

def getLocalName(corefile):
    data = os.popen("file " + corefile).readline()
    parts = data.split("'")
    if len(parts) == 3:
        return parts[1]
    else:
        newParts = data.split()
        if len(newParts) > 2 and newParts[-2].endswith(","):
            # AIX...
            return newParts[-1]
        else:
            return ""

def getLastFileName(corefile):
    # Yes, we know this is horrible. Does anyone know a better way of getting the binary out of a core file???
    # Unfortunately running gdb is not the answer, because it truncates the data...
    localName = getLocalName(corefile)
    possibleNames = os.popen("strings " + corefile + " | grep '^/.*/" + localName + "'").readlines()
    possibleNames.reverse()
    for name in possibleNames:
        name = name.strip()
        if os.path.isfile(name):
            return name
    # If none of them exist, return the first one anyway for error printout
    if len(possibleNames) > 0:
        return possibleNames[0].strip()
    else:
        return ""
    
def getBinary(corefile):
    binary = getLastFileName(corefile)
    if os.path.isfile(binary):
        return binary
    dirname, local = os.path.split(binary)
    parts = local.split(".")
    # pick up temporary binaries (Carmen-hack...)
    if len(parts) > 2 and len(parts[0]) == 0 and parts[-2] == os.getenv("USER"):
        return os.path.join(dirname, ".".join(parts[1:-2]))
    else:
        return binary

def writeCmdFile():
    fileName = mktemp("coreCommands.gdb")
    file = open(fileName, "w")
    file.write("bt\n")
    file.close()
    return fileName

def parseGdbOutput(stdout):
    summaryLine = ""
    signalDesc = ""
    stackLines = []
    prevLine = ""
    stackStarted = False
    for line in stdout.readlines():
        if line.find("Program terminated") != -1:
            summaryLine = line.strip()
            signalDesc = summaryLine.split(",")[-1].strip().replace(".", "")
        if line.startswith("#"):
            stackStarted = True
        if stackStarted and line != prevLine:
            methodName = line.rstrip()
            startPos = methodName.find("in ")
            if startPos != -1:
                methodName = methodName[startPos + 3:]
                stackLines.append(methodName)
            else:
                stackLines.append(methodName)
        prevLine = line
        
    if len(stackLines) > 1:
        signalDesc += " in " + getGdbMethodName(stackLines[0])

    return signalDesc, summaryLine, stackLines    

def parseDbxOutput(stdout):
    summaryLine = ""
    signalDesc = ""
    stackLines = []
    prevLine = ""
    for line in stdout.readlines():
        stripLine = line.strip()
        if line.find("program terminated") != -1:
            summaryLine = stripLine
            signalDesc = summaryLine.split("(")[-1].replace(")", "")
        if (stripLine.startswith("[") or stripLine.startswith("=>[")) and line != prevLine:
            startPos = line.find("]") + 2
            endPos = line.rfind("(")
            methodName = line[startPos:endPos]
            stackLines.append(methodName)
        prevLine = line

    if len(stackLines) > 1:
        signalDesc += " in " + stackLines[0].strip()
        
    return signalDesc, summaryLine, stackLines    

def getGdbMethodName(line):
    endPos = line.rfind("(")
    methodName = line[:endPos]
    pointerPos = methodName.find("+0")
    if pointerPos != -1:
        methodName = methodName[:pointerPos]
    return methodName.strip()

def parseFailure(errMsg, debugger):
    summary = "Parse failure on " + debugger + " output"
    if len(errMsg) > 50000:
        return summary, "Over 50000 error characters printed - suspecting binary output"
    else:
        return summary, debugger + " backtrace command failed : Stack trace not produced for crash\nErrors from " + debugger + ":\n" + errMsg


def assembleInfo(signalDesc, summaryLine, stackLines, debugger):
    summary = signalDesc
    details = summaryLine + "\nStack trace from " + debugger + " :\n" + \
              "\n".join(stackLines[:100])
    # Sometimes you get enormous stacktraces from GDB, for example, if you have
    # an infinite recursive loop.
    if len(stackLines) > 100:
        details += "\nStack trace print-out aborted after 100 function calls"
    return summary, details


def writeGdbStackTrace(corefile, binary):
    fileName = writeCmdFile()
    gdbCommand = "gdb -q -batch -x " + fileName + " " + binary + " " + corefile
    stdin, stdout, stderr = os.popen3(gdbCommand)
    signalDesc, summaryLine, stackLines = parseGdbOutput(stdout)
    os.remove(fileName)
    if summaryLine:
        return assembleInfo(signalDesc, summaryLine, stackLines, "GDB")
    else:
        return parseFailure(stderr.read(), "GDB")

def writeDbxStackTrace(corefile, binary):
    dbxCommand = "dbx -f -q -c 'where; quit' " + binary + " " + corefile + " < /dev/null"
    stdin, stdout, stderr = os.popen3(dbxCommand)
    signalDesc, summaryLine, stackLines = parseDbxOutput(stdout)
    if summaryLine:
        return assembleInfo(signalDesc, summaryLine, stackLines, "DBX")
    else:
        return parseFailure(stderr.read(), "DBX")

def printCoreInfo(corefile):
    compression = corefile.endswith(".Z")
    if compression:
        os.system("uncompress " + corefile)
        corefile = corefile[:-2]
    summary, details, binary = interpretCore(corefile)
    print summary
    print "-" * len(summary)
    print "(Core file at", corefile + ")"
    if binary:
        print "(Created by binary", binary + ")"
    print details
    if compression:
        os.system("compress " + corefile)

if len(sys.argv) != 2:
    print "Usage: interpretcore.py <corefile>"
else:
    corefile = sys.argv[1]
    if os.path.isfile(corefile):
        printCoreInfo(corefile)
    else:    
        sys.stderr.write("File not found : " + corefile + "\n")