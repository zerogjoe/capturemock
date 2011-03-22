
""" Class to handle the interface with the rc file """
from ConfigParser import ConfigParser
import os, logging.config

REPLAY = 0
RECORD = 1
REPLAY_OLD_RECORD_NEW = 2

class CaptureMockReplayError(RuntimeError):
    pass

class RcFileHandler:
    def __init__(self, rcFiles):
        self.parser = ConfigParser()
        if not rcFiles:
            rcFiles = self.getPersonalPath("config")
        self.parser.read(rcFiles)

    def getPersonalPath(self, fileName):
        return os.path.join(os.path.expanduser("~/.capturemock"), fileName)

    def getIntercepts(self, section):
        return self.getList("intercepts", [ section ])

    def get(self, *args):
        return self._get(self.parser.get, *args)

    def getboolean(self, *args):
        return self._get(self.parser.getboolean, *args)

    def _get(self, getMethod, setting, sections, defaultVal=None):
        for section in sections:
            if self.parser.has_section(section) and self.parser.has_option(section, setting):
                return getMethod(section, setting)
        return defaultVal

    def getList(self, setting, sections):
        result = []
        for section in sections:
            if self.parser.has_section(section) and self.parser.has_option(section, setting):
                listStr = self.parser.get(section, setting).strip()
                if listStr:
                    result += listStr.split(",")
        return result

    def setUpLogging(self):
        logConfigFile = self.get("log_config_file", [ "general" ],
                                 self.getPersonalPath("logging.conf"))
        if os.path.isfile(logConfigFile):
            defaults = { "LOCAL_DIR" : os.path.dirname(logConfigFile) }
            logging.config.fileConfig(logConfigFile, defaults)


def isActive(mode, replayFile):
    return mode != REPLAY or (replayFile is not None and os.path.isfile(replayFile))