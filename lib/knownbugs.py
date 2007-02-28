#!/usr/bin/env python

import plugins, os, string
from ConfigParser import ConfigParser, NoOptionError
from copy import copy
from ndict import seqdict

plugins.addCategory("bug", "known bugs", "had known bugs")
plugins.addCategory("badPredict", "internal errors", "had internal errors")
plugins.addCategory("crash", "CRASHED")

# For backwards compatibility...
class FailedPrediction(plugins.TestState):
    def getTypeBreakdown(self):
        if self.category == "bug":
            return "success", self.briefText
        else:
            return "failure", self.briefText

class Bug:
    def findCategory(self, internalError):
        if internalError:
            return "badPredict"
        else:
            return "bug"

class BugSystemBug(Bug):
    def __init__(self, bugSystem, bugId):
        self.bugId = bugId
        self.bugSystem = bugSystem
    def findInfo(self):
        exec "from " + self.bugSystem + " import findBugText, findStatus, isResolved"
        bugText = findBugText(self.bugId)
        status = findStatus(bugText)
        category = self.findCategory(isResolved(status))
        briefText = "bug " + self.bugId + " (" + status + ")"
        return category, briefText, self.getFullText(status, bugText)
    def getFullText(self, status, description):
        if status == "UNKNOWN":
            return "Could not contact " + self.bugSystem + " to extract information about bug " + self.bugId
        elif status == "NONEXISTENT":
            return "Bug " + self.bugId + " does not exist in " + self.bugSystem
        else:
            return description
    
class UnreportedBug(Bug):
    def __init__(self, fullText, briefText, internalError):
        self.fullText = fullText
        self.briefText = briefText
        self.internalError = internalError
    def findInfo(self):
        return self.findCategory(self.internalError), self.briefText, self.fullText

class BugTrigger:
    def __init__(self, getOption):
        self.textTrigger = plugins.TextTrigger(getOption("search_string"))
        self.triggerHosts = self.getTriggerHosts(getOption)
        self.checkUnchanged = int(getOption("trigger_on_success", "0"))
        self.ignoreOtherErrors = int(getOption("internal_error", "0"))
        self.bugInfo = self.createBugInfo(getOption)
        self.diag = plugins.getDiagnostics("Check For Bugs")
    def getTriggerHosts(self, getOption):
        hostStr = getOption("execution_hosts")
        if hostStr:
            return hostStr.split(",")
        else:
            return []
    def createBugInfo(self, getOption):
        bugSystem = getOption("bug_system")
        if bugSystem:
            return BugSystemBug(bugSystem, getOption("bug_id"))
        else:
            return UnreportedBug(getOption("full_description"), getOption("brief_description"), self.ignoreOtherErrors)
    def hasText(self, bugText):
        return self.textTrigger.text == bugText
    def matchesText(self, line):
        return self.textTrigger.matches(line)
    def findBug(self, execHosts, isChanged, multipleDiffs, line=None):
        if not self.checkUnchanged and not isChanged:
            self.diag.info("File not changed, ignoring")
            return
        if multipleDiffs and not self.ignoreOtherErrors:
            self.diag.info("Multiple differences present, allowing others through")
            return
        if line is not None and not self.textTrigger.matches(line):
            return
        if self.hostsMatch(execHosts):
            return self.bugInfo
        else:
            self.diag.info("No match " + repr(execHosts) + " with " + repr(self.triggerHosts))
    def hostsMatch(self, execHosts):
        if len(self.triggerHosts) == 0:
            return True
        for host in execHosts:
            if not host in self.triggerHosts:
                return False
        return True

class FileBugData:
    def __init__(self):
        self.presentList = []
        self.absentList = []
        self.checkUnchanged = False
        self.diag = plugins.getDiagnostics("Check For Bugs")
    def addBugTrigger(self, getOption):
        bugTrigger = BugTrigger(getOption)
        if bugTrigger.checkUnchanged:
            self.checkUnchanged = True
        if getOption("trigger_on_absence", False):
            self.absentList.append(bugTrigger)
        else:
            self.presentList.append(bugTrigger)
    def insert(self, bugData):
        self.presentList += bugData.presentList
        self.absentList += bugData.absentList
        self.checkUnchanged |= bugData.checkUnchanged
    def remove(self, bugData):
        for trigger in bugData.presentList:
            self.presentList.remove(trigger)
        for trigger in bugData.absentList:
            self.absentList.remove(trigger)
        if bugData.checkUnchanged:
            self.checkUnchanged = False
            for trigger in self.presentList + self.absentList:
                if trigger.checkUnchanged:
                    self.checkUnchanged = True
    def findBug(self, fileName, execHosts, isChanged, multipleDiffs):
        self.diag.info("Looking for bugs in " + fileName)
        if not self.checkUnchanged and not isChanged:
            self.diag.info("File not changed, ignoring")
            return
        if not os.path.isfile(fileName):
            self.diag.info("File doesn't exist, checking only for absence bugs")
            return self.findAbsenceBug(self.absentList, execHosts, isChanged, multipleDiffs)
        
        return self.findBugInText(open(fileName).readlines(), execHosts, isChanged, multipleDiffs)
    def findBugInText(self, lines, execHosts, isChanged=True, multipleDiffs=False):
        currAbsent = copy(self.absentList)
        for line in lines:
            for bugTrigger in self.presentList:
                bug = bugTrigger.findBug(execHosts, isChanged, multipleDiffs, line)
                if bug:
                    return bug
            for bugTrigger in currAbsent:
                if bugTrigger.matchesText(line):
                    currAbsent.remove(bugTrigger)
                    break

        return self.findAbsenceBug(currAbsent, execHosts, isChanged, multipleDiffs)
    def findAbsenceBug(self, absentList, execHosts, isChanged, multipleDiffs):
        for bugTrigger in absentList:
            bug = bugTrigger.findBug(execHosts, isChanged, multipleDiffs)
            if bug:
                return bug

class ParseMethod:
    def __init__(self, parser, section):
        self.parser = parser
        self.section = section
    def __call__(self, option, default=""):
        try:
            return self.parser.get(self.section, option)
        except NoOptionError:
            return default

class BugMap(seqdict):
    def checkUnchanged(self):
        for bugData in self.values():
            if bugData.checkUnchanged:
                return True
        return False
    def readFromFile(self, fileName):
        parser = self.makeParser(fileName)
        if parser:
            self.readFromParser(parser)
    def makeParser(self, fileName):
        parser = ConfigParser()
        # Default behaviour transforms to lower case: we want case-sensitive
        parser.optionxform = str
        try:
            parser.read(fileName)
            return parser
        except:
            print "Bug file at", fileName, "not understood, ignoring"
    def readFromParser(self, parser):
        for section in parser.sections():
            getOption = ParseMethod(parser, section)
            fileStem = getOption("search_file")
            if not self.has_key(fileStem):
                self[fileStem] = FileBugData()
            self[fileStem].addBugTrigger(getOption)
    def insert(self, bugMap):
        for fileStem, testBugData in bugMap.items():
            if self.has_key(fileStem):
                self[fileStem].insert(testBugData)
            else:
                self[fileStem] = testBugData
    def remove(self, bugMap):
        for fileStem, testBugData in bugMap.items():
            self[fileStem].remove(testBugData)

class CheckForCrashes(plugins.Action):
    def __init__(self):
        self.diag = plugins.getDiagnostics("check for crashes")
    def __call__(self, test):
        if test.state.category == "killed":
            return
        # Hard-coded prediction: check test didn't crash
        comparison, newList = test.state.findComparison("stacktrace")
        if comparison:
            stackTraceFile = comparison.tmpFile
            self.diag.info("Parsing " + stackTraceFile)
            summary, errorInfo = self.parseStackTrace(test, stackTraceFile)
            newState = copy(test.state)
            comparison, newList = newState.findComparison("stacktrace")
            newList.remove(comparison)
            crashState = FailedPrediction("crash", errorInfo, summary)
            newState.setFailedPrediction(crashState)
            test.changeState(newState)
            os.remove(stackTraceFile)
    def parseStackTrace(self, test, stackTraceFile):
        lines = open(stackTraceFile).readlines()
        if len(lines) > 2:
            return lines[0].strip(), string.join(lines[2:], "")
        else:
            errFile = test.makeTmpFileName("stacktrace.collate_errs", forFramework=1)
            return "core not parsed", "Errors from collation script:\n" + open(errFile).read()

class CheckForBugs(plugins.Action):
    def __init__(self):
        self.activeBugs = BugMap()
        self.testBugMap = {} # map from test to BugMap
        self.diag = plugins.getDiagnostics("Check For Bugs")
    def setUpSuite(self, suite):
        self.readBugs(suite)
        self.activateBugs(suite)
    def tearDownSuite(self, suite):
        self.deactivateBugs(suite)
    def callDuringAbandon(self, test):
        # want to be able to mark UNRUNNABLE tests as known bugs too...
        return test.state.lifecycleChange != "complete"
    def __call__(self, test):
        self.readBugs(test)
        if not self.checkTest(test):
            return

        self.activateBugs(test)
        multipleDiffs = self.hasMultipleDifferences(test)
        for stem, fileBugData in self.activeBugs.items():
            bug = self.findBug(test, stem, fileBugData, multipleDiffs)
            if bug:
                category, briefText, fullText = bug.findInfo()
                self.diag.info("Changing to " + category + " with text " + briefText)
                bugState = FailedPrediction(category, fullText, briefText, completed=1)
                self.changeState(test, bugState)
                break # no point searching for more bugs...
        self.deactivateBugs(test)
    def findBug(self, test, stem, fileBugData, multipleDiffs):
        self.diag.info("Looking for bugs in file " + stem)
        if stem == "free_text":
            return fileBugData.findBugInText(test.state.freeText.split("\n"), test.state.executionHosts)
        elif test.state.hasResults():
            # bugs are only relevant if the file itself is changed, unless marked to trigger on success also
            isChanged = self.fileChanged(test, stem)
            fileName = test.makeTmpFileName(stem)
            return fileBugData.findBug(fileName, test.state.executionHosts, isChanged, multipleDiffs)
    def changeState(self, test, bugState):
        if hasattr(test.state, "failedPrediction"):
            # if we've already compared, slot our things into the comparison object
            newState = copy(test.state)
            newState.setFailedPrediction(bugState)
            test.changeState(newState)
        else:
            test.changeState(bugState)
    def hasMultipleDifferences(self, test):
        if not test.state.hasResults():
            # check for unrunnables...
            return False
        comparisons = test.state.getComparisons()
        diffCount = len(comparisons)
        if diffCount <= 1:
            return False
        perfStems = test.state.getPerformanceStems(test)
        for comp in comparisons:
            if comp.stem in perfStems:
                diffCount -= 1
        return diffCount > 1
    def checkTest(self, test):
        if self.activeBugs.checkUnchanged() or self.testBugMap[test].checkUnchanged():
            return True
        return test.state.hasFailed()
    def fileChanged(self, test, stem):
        comparison, list = test.state.findComparison(stem)
        return bool(comparison)
    def makeBugMap(self, suite):
        bugFile = suite.getFileName("knownbugs")
        bugMap = BugMap()
        if bugFile:
            self.diag.info("Reading bugs from file " + bugFile)
            bugMap.readFromFile(bugFile)
        return bugMap
    def readBugs(self, suite):
        if not self.testBugMap.has_key(suite):
            self.testBugMap[suite] = self.makeBugMap(suite)
    def activateBugs(self, suite):     
        self.activeBugs.insert(self.testBugMap[suite])
    def deactivateBugs(self, suite):
        self.activeBugs.remove(self.testBugMap[suite])
            
class MigrateFiles(plugins.Action):
    def setUpSuite(self, suite):
        self.migrate(suite)
    def __call__(self, test):
        self.migrate(test)
    def __repr__(self):
        return "Migrating knownbugs file in"
    def migrate(self, test):
        for bugFileName in test.findAllStdFiles("knownbugs"):
            parser = ConfigParser()
            # Default behaviour transforms to lower case: we want case-sensitive
            parser.optionxform = str
            try:
                parser.read(bugFileName)
            except:
                print "Bug file at", bugFileName, "not understood, ignoring"
                continue
            if not parser.has_section("Migrated section 1"):
                self.describe(test, " - " + os.path.basename(bugFileName))
                self.updateFile(bugFileName, parser)
            else:
                self.describe(test, " (already migrated)")
    def updateFile(self, bugFileName, parser):
        newBugFileName = bugFileName + ".new"
        newBugFile = open(newBugFileName, "w")
        self.writeNew(parser, newBugFile)
        newBugFile.close()
        os.system("diff " + bugFileName + " " + newBugFileName)
        os.rename(newBugFileName, bugFileName)
    def writeNew(self, parser, newBugFile):
        sectionNo = 0
        for fileStem in parser.sections():
            for bugText in parser.options(fileStem):
                bugId = parser.get(fileStem, bugText)
                sectionNo += 1
                self.writeSection(newBugFile, sectionNo, fileStem, bugText, bugId)
    def writeSection(self, newBugFile, sectionNo, fileStem, bugText, bugId):
        newBugFile.write("[Migrated section " + str(sectionNo) + "]\n")
        newBugFile.write("search_string:" + bugText + "\n")
        newBugFile.write("search_file:" + fileStem + "\n")
        bugSystem = self.findBugSystem(bugId)
        if bugSystem:
            newBugFile.write("bug_system:" + bugSystem + "\n")
            newBugFile.write("bug_id:" + bugId + "\n")
        else:
            newBugFile.write("full_description:" + bugId + "\n")
            newBugFile.write("brief_description:unreported bug\n")
            newBugFile.write("internal_error:0\n")
        newBugFile.write("\n")
    def findBugSystem(self, bugId):
        for letter in bugId:
            if not letter in string.digits:
                return None
        return "bugzilla"

class MigrateInternalErrors(plugins.Action):
    def setUpApplication(self, app):
        errNo = 0
        fileName = os.path.join(app.getDirectory(), "knownbugs." + app.name + app.versionSuffix())
        print "Writing to", fileName
        writeFile = open(fileName, "a")
            
        for text in app.getConfigValue("internal_error_text"):
            errNo += 1
            self.writeSection(writeFile, errNo, text, absent=False)
        for text in app.getConfigValue("internal_compulsory_text"):
            errNo += 1
            self.writeSection(writeFile, errNo, text, absent=True)
    def writeSection(self, writeFile, errNo, text, absent):
        writeFile.write("\n[Migrated internal error " + str(errNo) + "]\n")
        writeFile.write("search_string:" + text + "\n")
        writeFile.write("search_file:output\n")
        if absent:
            writeFile.write("trigger_on_absence:1\n")
        writeFile.write("full_description:" + text + "\n")
        writeFile.write("brief_description:" + text + "\n")
        writeFile.write("internal_error:1\n")