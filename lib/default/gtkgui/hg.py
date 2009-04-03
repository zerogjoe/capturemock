
import version_control, plugins, shutil, datetime, time, os

class HgInterface(version_control.VersionControlInterface):
    def __init__(self, controlDir):
        self.warningStateInfo = { "M": "Modified", "R":"Removed", "A":"Added" }
        self.errorStateInfo = { "?" : "Unknown", "!" : "Missing" }
        version_control.VersionControlInterface.__init__(self, controlDir, "Mercurial",
                                                         self.warningStateInfo.values(), self.errorStateInfo.values(), "tip")
        self.defaultArgs["rm"] = [ "--force" ]
        self.defaultArgs["log"] = [ "-f" ] # "follow" renames, which isn't default
        self.defaultArgs["annotate"] = [ "-f", "-n" ] # annotate -f doesn't annotate anything...
        self.defaultArgs["diff"] = [ "-g" ] # "git format", which apparently is how to make it work with revisions :)
        
    def getDateFromLog(self, output):
        for line in output.splitlines():
            if line.startswith("date:"):
                dateStr = " ".join(line.split()[2:-1])
                return datetime.datetime(*(self.parseDateTime(dateStr)[0:6]))

    def getGraphicalDiffArgs(self, diffProgram):
        return [ "hg", "extdiff", "-p", diffProgram ]

    def parseDateTime(self, input):
        return time.strptime(input, "%b %d %H:%M:%S %Y")

    def getStateFromStatus(self, output):
        if len(output) == 0:
            return "Unchanged"
        statusLetter = output.split()[0]
        return self.warningStateInfo.get(statusLetter, self.errorStateInfo.get(statusLetter, statusLetter))
        
    def moveDirectory(self, oldDir, newDir):
        # Moving doesn't work in hg if there are symbolic links in the path to the new location!
        retCode = self.callProgram("mv", [ oldDir, os.path.realpath(newDir) ])
        if retCode == 0:
            # And it doesn't take non-versioned files with it...
            self.copyDirectory(oldDir, newDir)
            shutil.rmtree(oldDir)
        else:
            # Wasn't in version control, probably
            os.rename(oldDir, newDir)

    def removePath(self, path):
        retCode = self.callProgram("rm", [ path ])
        if retCode > 0:
            # Wasn't in version control, probably
            return plugins.removePath(path)
        else:
            plugins.removePath(path) # Mercurial doesn't remove unknown files
            return True


version_control.vcsClass = HgInterface

class AnnotateGUI(version_control.AnnotateGUI):
    def commandHadError(self, retcode, stderr):
        return len(stderr) > 0 # Mercurial doesn't do return codes in annotate for some reason...

class AnnotateGUIRecursive(AnnotateGUI):
    recursive = True

#
# Configuration for the Interactive Actions
#
class InteractiveActionConfig(version_control.InteractiveActionConfig):
    def annotateClasses(self):
        return [ AnnotateGUI, AnnotateGUIRecursive ]
