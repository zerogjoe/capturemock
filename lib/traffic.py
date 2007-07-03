#!/usr/bin/env python

import os, sys, plugins, shutil, socket, subprocess
from copy import deepcopy
from ndict import seqdict
from SocketServer import TCPServer, StreamRequestHandler
from threading import Thread
from types import StringType

class MethodWrap:
    def __init__(self, method, firstArg):
        self.method = method
        self.firstArg = firstArg
    def __call__(self, arg):
        return self.method(self.firstArg, arg)

class SetUpTrafficHandlers(plugins.Action):
    def __init__(self, record):
        self.record = record
        self.trafficFile = self.findTrafficFile()
    def findTrafficFile(self):
        for dir in sys.path:
            fullPath = os.path.join(dir, "traffic_cmd.py")
            if os.path.isfile(fullPath):
                return fullPath
    def __call__(self, test):
        if self.configureServer(test):
            self.makeIntercepts(test)
    def configureServer(self, test):
        recordFile = test.makeTmpFileName("traffic")
        envVarMethod = MethodWrap(test.getCompositeConfigValue, "collect_traffic_environment")
        if self.record:
            self.setServerState(recordFile, None, envVarMethod)
            return True
        else:
            trafficReplay = test.getFileName("traffic")
            if trafficReplay:
                self.setServerState(recordFile, trafficReplay, envVarMethod)
                return True
            else:
                self.setServerState(None, None, envVarMethod)
                return False
    def setServerState(self, recordFile, replayFile, envVarMethod):
        if recordFile or replayFile and not TrafficServer.instance:
            TrafficServer.instance = TrafficServer()
        if TrafficServer.instance:
            TrafficServer.instance.setState(recordFile, replayFile, envVarMethod)
    def makeIntercepts(self, test):
        for cmd in test.getConfigValue("collect_traffic"):
            linkName = test.makeTmpFileName(cmd, forComparison=0)
            self.intercept(test, linkName)
    def intercept(self, test, linkName):
        if os.path.exists(linkName):
            # We might have written a fake version - store what it points to so we can
            # call it later, and remove the link
            localName = os.path.basename(linkName)
            TrafficServer.instance.setRealVersion(localName, test.makePathName(localName))
            os.remove(linkName)
        # Linking doesn't exist on windows!
        if os.name == "posix":
            os.symlink(self.trafficFile, linkName)
        else:
            shutil.copy(self.trafficFile, linkName)

class Traffic:
    def __init__(self, text, responseFile):
        self.text = text
        self.responseFile = responseFile
    def hasInfo(self):
        return len(self.text) > 0
    def getDescription(self):
        return self.direction + self.typeId + ":" + self.text
    def forwardToDestination(self):
        if self.responseFile:
            self.responseFile.write(self.text)
            self.responseFile.close()
        return []
    def filterReplay(self, trafficList):
        return trafficList
    
class ResponseTraffic(Traffic):
    direction = "->"

class StdoutTraffic(ResponseTraffic):
    typeId = "OUT"
    def forwardToDestination(self):
        if self.responseFile:
            self.responseFile.write(self.text + "|TT_CMD_SEP|")
        return []

class StderrTraffic(ResponseTraffic):
    typeId = "ERR"
    def forwardToDestination(self):
        if self.responseFile:
            self.responseFile.write(self.text + "|TT_CMD_SEP|")
        return []

class SysExitTraffic(ResponseTraffic):
    typeId = "EXC"
    def __init__(self, status, responseFile):
        ResponseTraffic.__init__(self, str(status), responseFile)
        self.exitStatus = int(status)
    def hasInfo(self):
        return self.exitStatus != 0
    
class ClientSocketTraffic(Traffic):
    destination = None
    direction = "<-"
    typeId = "CLI"
    def forwardToDestination(self):
        if self.destination:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(self.destination)
            sock.sendall(self.text)
            sock.shutdown(socket.SHUT_WR)
            response = sock.makefile().read()
            sock.close()
            return [ ServerTraffic(response, self.responseFile) ]
        else:
            return [] # client is alone, nowhere to forward

class ServerTraffic(Traffic):
    typeId = "SRV"
    direction = "->"

class ServerStateTraffic(ServerTraffic):
    def __init__(self, inText, responseFile):
        ServerTraffic.__init__(self, inText, responseFile)
        if not ClientSocketTraffic.destination:
            lastWord = inText.strip().split()[-1]
            host, port = lastWord.split(":")
            ClientSocketTraffic.destination = host, int(port)
            # If we get a server state message, switch the order around
            ClientSocketTraffic.direction = "->"
            ServerTraffic.direction = "<-"
    def forwardToDestination(self):
        return []
            
class CommandLineTraffic(Traffic):
    typeId = "CMD"
    direction = "<-"
    envVarMethod = None
    origEnviron = {}
    realCommands = {}
    def __init__(self, inText, responseFile):
        cmdText, environText, cmdCwd = inText.split(":SUT_SEP:")
        argv = eval(cmdText)
        self.cmdEnviron = eval(environText)
        self.cmdCwd = cmdCwd
        self.fullCommand = argv[0].replace("\\", "/")
        self.commandName = os.path.basename(self.fullCommand)
        self.cmdArgs = argv[1:]
        self.argStr = " ".join(map(self.quote, argv[1:]))
        self.environ = self.filterEnvironment(self.cmdEnviron)
        self.diag = plugins.getDiagnostics("Traffic Server")
        self.path = self.cmdEnviron.get("PATH")
        text = self.getEnvString() + self.commandName + " " + self.argStr
        Traffic.__init__(self, text, responseFile)
    def filterEnvironment(self, cmdEnviron):
        interestingEnviron = []
        for var in self.envVarMethod(self.commandName):
            value = cmdEnviron.get(var)
            if value is not None:
                interestingEnviron.append((var, value))
        return interestingEnviron
    def getEnvString(self):
        if len(self.environ) == 0:
            return ""
        recStr = "env "
        for var, value in self.environ:
            recLine = "'" + var + "=" + value + "' "
            oldVal = self.origEnviron.get(var)
            if oldVal and oldVal != value:
                recLine = recLine.replace(oldVal, "$" + var)
            recStr += recLine
        return recStr
    def getQuoteChar(self, char):
        if char == "\"" and os.name == "posix":
            return "'"
        else:
            return '"'
    def quote(self, arg):
        quoteChars = "'\"|* "
        for char in quoteChars:
            if char in arg:
                quoteChar = self.getQuoteChar(char)
                return quoteChar + arg + quoteChar
        return arg
    def forwardToDestination(self):
        realCmd = self.findRealCommand()
        if realCmd:                
            fullArgs = [ realCmd ] + self.cmdArgs
            interpreter = plugins.getInterpreter(realCmd)
            if interpreter:
                fullArgs = [ interpreter ] + fullArgs
            proc = subprocess.Popen(fullArgs, env=self.cmdEnviron, cwd=self.cmdCwd, 
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            output, errors = proc.communicate()
            return self.makeResponse(output, errors, proc.returncode)
        else:
            return self.makeResponse("", "ERROR: Traffic server could not find command '" + self.commandName + "' in PATH", 1)
    def makeResponse(self, output, errors, exitCode):
        return [ StdoutTraffic(output, self.responseFile), StderrTraffic(errors, self.responseFile), \
                 SysExitTraffic(exitCode, self.responseFile) ]
    def findRealCommand(self):
        # If we found a link already, use that, otherwise look on the path
        if self.realCommands.has_key(self.commandName):
            return self.realCommands[self.commandName]
        # Find the first one in the path that isn't us :)
        TrafficServer.instance.diag.info("Finding real command to replace " + self.fullCommand)
        for currDir in self.path.split(os.pathsep):
            TrafficServer.instance.diag.info("Searching " + currDir)
            fullPath = os.path.join(currDir, self.commandName)
            if self.isRealCommand(fullPath):
                return fullPath
    def isRealCommand(self, fullPath):
        return os.path.isfile(fullPath) and os.access(fullPath, os.X_OK) and \
               not plugins.samefile(fullPath, self.fullCommand)
    def filterReplay(self, trafficList):
        if len(trafficList) == 0 or not isinstance(trafficList[0], StdoutTraffic):
            trafficList.insert(0, StdoutTraffic("", self.responseFile))
        if len(trafficList) == 1 or not isinstance(trafficList[1], StderrTraffic):
            trafficList.insert(1, StderrTraffic("", self.responseFile))
        if len(trafficList) == 2 or not isinstance(trafficList[2], SysExitTraffic):
            trafficList.insert(2, SysExitTraffic("0", self.responseFile))
        for extraTraffic in trafficList[3:]:
            extraTraffic.responseFile = None
        return trafficList
                               
class TrafficRequestHandler(StreamRequestHandler):
    parseDict = { "SUT_SERVER" : ServerStateTraffic, "SUT_COMMAND_LINE" : CommandLineTraffic }
    def handle(self):
        text = self.rfile.read()
        traffic = self.parseTraffic(text)
        self.server.process(traffic)
    def parseTraffic(self, text):
        for key in self.parseDict.keys():
            prefix = key + ":"
            if text.startswith(prefix):
                value = text[len(prefix):]
                return self.parseDict[key](value, self.wfile)
        return ClientSocketTraffic(text, self.wfile)
            
class TrafficServer(TCPServer):
    instance = None
    def __init__(self):
        self.recordFile = None
        self.replayInfo = None
        self.diag = plugins.getDiagnostics("Traffic Server")
        TrafficServer.instance = self
        TCPServer.__init__(self, (socket.gethostname(), 0), TrafficRequestHandler)
        self.setAddressVariable()
        self.thread = Thread(target=self.serve_forever)
        self.thread.setDaemon(1)
        self.thread.start()
    def setAddressVariable(self):
        host, port = self.socket.getsockname()
        address = host + ":" + str(port)
        os.environ["TEXTTEST_MIM_SERVER"] = address
        self.diag.info("Starting traffic server on " + address)
    def setRealVersion(self, command, realCommand):
        self.diag.info("Storing faked command for " + command + " = " + realCommand) 
        CommandLineTraffic.realCommands[command] = realCommand
    def setState(self, recordFile, replayFile, envVarMethod):
        self.recordFile = recordFile
        self.replayInfo = ReplayInfo(replayFile)
        if recordFile or replayFile:
            self.setAddressVariable()
        else:
            os.environ["TEXTTEST_MIM_SERVER"] = ""
        CommandLineTraffic.envVarMethod = envVarMethod
        CommandLineTraffic.origEnviron = deepcopy(os.environ)
        ClientSocketTraffic.destination = None
        # Assume testing client until a server contacts us
        ClientSocketTraffic.direction = "<-"
        ServerTraffic.direction = "->"
    def process(self, traffic):
        self.diag.info("Processing traffic " + repr(traffic.__class__))
        self.record(traffic)
        for response in self.replayInfo.getResponses(traffic):
            self.diag.info("Providing response " + repr(response.__class__))
            self.record(response)
            for chainResponse in response.forwardToDestination():
                self.process(chainResponse)
            self.diag.info("Completed response " + repr(response.__class__))
    def record(self, traffic):
        if not traffic.hasInfo():
            return
        desc = traffic.getDescription()
        if not desc.endswith("\n"):
            desc += "\n"
        self.diag.info("Recording " + repr(traffic.__class__) + " " + desc)
        writeFile = open(self.recordFile, "a")
        writeFile.write(desc)
        writeFile.flush()
        writeFile.close()

class ReplayInfo:
    def __init__(self, replayFile):
        self.responseMap = seqdict()
        self.diag = plugins.getDiagnostics("Traffic Replay")
        if replayFile:
            self.readReplayFile(replayFile)
    def readReplayFile(self, replayFile):
        trafficList = self.readIntoList(replayFile)
        currResponseHandler = None
        for trafficStr in trafficList:
            if trafficStr.startswith("<-"):
                if currResponseHandler:
                    currResponseHandler.endResponse()
                currTrafficIn = trafficStr.strip()
                if not self.responseMap.has_key(currTrafficIn):
                    self.responseMap[currTrafficIn] = ReplayedResponseHandler()
                currResponseHandler = self.responseMap[currTrafficIn]
            else:
                currResponseHandler.addResponse(trafficStr)
        self.diag.info("Replay info " + repr(self.responseMap))
    def readIntoList(self, replayFile):
        trafficList = []
        currTraffic = ""
        for line in open(replayFile).xreadlines():
            if line.startswith("<-") or line.startswith("->"):
                if currTraffic:
                    trafficList.append(currTraffic)
                currTraffic = ""
            currTraffic += line
        if currTraffic:
            trafficList.append(currTraffic)
        return trafficList
    def getResponses(self, traffic):
        if len(self.responseMap) > 0:
            return self.readReplayResponses(traffic)
        else:
            return traffic.forwardToDestination()
    def readReplayResponses(self, traffic):
        # We return the response matching the traffic in if we can, otherwise
        # the one that is most similar to it
        if not traffic.hasInfo():
            return []
        desc = traffic.getDescription()
        bestMatchKey = self.findBestMatch(desc)
        if bestMatchKey:
            return self.responseMap[bestMatchKey].makeResponses(traffic)
        else:
            return []
    def findBestMatch(self, desc):
        self.diag.info("Trying to match '" + desc + "'")
        if self.responseMap.has_key(desc):
            self.diag.info("Found exact match")
            return desc
        bestMatchPerc, bestMatch, fewestTimesChosen = 0.0, None, 100000
        for currDesc, responseHandler in self.responseMap.items():
            if not self.sameType(desc, currDesc):
                continue
            matchPerc = self.findMatchPercentage(currDesc, desc)
            self.diag.info("Match percentage " + repr(matchPerc) + " with '" + currDesc + "'")
            if matchPerc > bestMatchPerc or (matchPerc == bestMatchPerc and responseHandler.timesChosen < fewestTimesChosen):
                bestMatchPerc, bestMatch, fewestTimesChosen = matchPerc, currDesc, responseHandler.timesChosen
        if bestMatch is not None:
            self.diag.info("Best match chosen as '" + bestMatch + "'")
            return bestMatch
    def sameType(self, desc1, desc2):
        return desc1[2:5] == desc2[2:5]
    def getWords(self, desc):
        words = []
        for part in desc.split("/"):
            words += part.split()
        return words
    def findMatchPercentage(self, traffic1, traffic2):
        words1 = self.getWords(traffic1)
        words2 = self.getWords(traffic2)
        matches = 0
        for word in words1:
            if word in words2:
                matches += 1
        nomatches = len(words1) + len(words2) - (2 * matches)
        return 100.0 * float(matches) / float(nomatches + matches)
    

# Need to handle multiple replies to the same question
class ReplayedResponseHandler:
    def __init__(self):
        self.timesChosen = 0
        self.readIndex = 0
        self.responses = []
    def __repr__(self):
        return repr(self.responses)
    def endResponse(self):
        self.readIndex += 1
    def addResponse(self, trafficStr):
        if len(self.responses) <= self.readIndex:
            self.responses.append([])
        self.responses[self.readIndex].append(trafficStr)
    def getCurrentStrings(self):
        if len(self.responses) == 0:
            return []
        if self.timesChosen < len(self.responses):
            currStrings = self.responses[self.timesChosen]
        else:
            currStrings = self.responses[0]
        return currStrings
    def makeResponses(self, traffic):
        trafficStrings = self.getCurrentStrings()
        responses = []
        for trafficStr in trafficStrings:
            trafficType = trafficStr[2:5]
            allClasses = [ ClientSocketTraffic, ServerTraffic, StdoutTraffic, StderrTraffic, SysExitTraffic ]
            for trafficClass in allClasses:
                if trafficClass.typeId == trafficType:
                    responses.append(trafficClass(trafficStr[6:], traffic.responseFile))
        self.timesChosen += 1
        return traffic.filterReplay(responses)
