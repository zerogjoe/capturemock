
import logging, os
import plugins
from ndict import seqdict
from batchutils import calculateBatchDate
from string import Template

class JUnitResponder(plugins.Responder):
    """Respond to test results and write out results in format suitable for JUnit
    report writer. Only does anything if the app has batch_junit_format:true in its config file """
    
    def __init__(self, optionMap, *args):
        self.sessionName = optionMap["b"]
        self.runId = optionMap.get("name", calculateBatchDate()) # use the command-line name if given, else the date
        self.allApps = seqdict()
        self.appData = seqdict()

    def useJUnitFormat(self, app):
        return app.getCompositeConfigValue("batch_junit_format", self.sessionName) == "true"
    
    def notifyComplete(self, test):
        if not self.useJUnitFormat(test.app):
            return
        if not self.appData.has_key(test.app):
            self._addApplication(test)
        self.appData[test.app].storeResult(test)
        
    def notifyAllComplete(self):
        # allApps is {appname : [app]}
        for appname, appList in self.allApps.items():
            # appData is {app : data}
            for app in appList:
                if self.useJUnitFormat(app):
                    data = self.appData[app]
                    ReportWriter(self.sessionName, self.runId).writeResults(app, data)
      
    def _addApplication(self, test):
        app = test.app
        self.appData[app] = JUnitApplicationData()
        self.allApps.setdefault(app.name, []).append(app)


class JUnitApplicationData:
    """Data class to store test results in a format convenient for conversion to 
    JUnit report format """
    def __init__(self):
        self.testResults = {}
        
    def storeResult(self, test):
        result = dict(full_test_name=self._fullTestName(test), 
                      test_name=test.name,
                      time="1") # fake the time
        if not test.state.hasResults():
            self._error(test, result)
        elif test.state.hasSucceeded():
            self._success(test, result)
        else:
            self._failure(test, result)
        
        self.testResults[test.name] = result
        
    def getResults(self):
        return self.testResults
    
    def _fullTestName(self, test):
        relpath = test.getRelPath()
        return test.app.fullName() + "." + relpath.replace("/", ".")
    
    def _error(self, test, result):
        result["error"] = True
        result["success"] = False
        result["failure"] = False                    
        result["short_message"] = self._shortMessage(test)
        result["long_message"] = self._longMessage(test)                   

    def _success(self, test, result):
        result["error"] = False
        result["success"] = True
        result["failure"] = False        
        
    def _failure(self, test, result):
        result["error"] = False
        result["success"] = False
        result["failure"] = True
        result["short_message"] = self._shortMessage(test)
        result["long_message"] = self._longMessage(test)                    
    
    def _shortMessage(self, test):
        overall, postText = test.state.getTypeBreakdown()
        return postText 
    
    def _longMessage(self, test):
        message = test.state.freeText.replace("]]>", "END_MARKER")
        return message       


failure_template = """\
<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="$full_test_name" failures="1" tests="1" time="$time" errors="0">
  <properties/>
  <testcase name="$test_name" time="$time" classname="">
    <failure type="differences" message="$short_message">
    <![CDATA[
$long_message
]]>
    </failure>
  </testcase>
</testsuite>
"""

error_template = """\
<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="$full_test_name" failures="0" tests="1" time="$time" errors="1">
  <properties/>
  <testcase name="$test_name" time="$time" classname="">
    <error type="none" message="$short_message">
    <![CDATA[
$long_message
]]>
    </error>
  </testcase>
</testsuite>
"""

success_template = """\
<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="$full_test_name" failures="0" tests="1" time="$time" errors="0">
  <properties/>
  <testcase name="$test_name" time="$time" classname=""/>
</testsuite>
"""

class ReportWriter:
    def __init__(self, sessionName, runId):
        self.sessionName = sessionName
        self.runId = runId
        self.diag = logging.getLogger("JUnit Report Writer")
        
    def writeResults(self, app, appData):
        self.diag.info("writing results in junit format for app " + app.fullName())
        appResultsDir = self._createResultsDir(app)
        for testName, result in appData.getResults().items():
            if result["success"]:
                text = Template(success_template).substitute(result)
            elif result["error"]:
                text = Template(error_template).substitute(result)
            else:
                text = Template(failure_template).substitute(result)
            testFileName = os.path.join(appResultsDir, testName + ".xml")
            self._writeTestResult(testFileName, text)
            
    def _writeTestResult(self, testFileName, text):
        testFile = open(testFileName, "w")
        testFile.write(text)
        testFile.close()        
            
    def _createResultsDir(self, app):
        resultsDir = self.userDefinedFolder(app)
        if (resultsDir is None or resultsDir.strip() == ""):
            resultsDir = os.path.join(app.writeDirectory, "junitreport")
    
        if not os.path.exists(resultsDir):
            os.mkdir(resultsDir)
        appResultsDir = os.path.join(resultsDir, app.name + app.versionSuffix())
        if not os.path.exists(appResultsDir):
            os.mkdir(appResultsDir)
        return appResultsDir
            
    def userDefinedFolder(self, app):
        return app.getCompositeConfigValue("batch_junit_folder", self.sessionName)
         
        