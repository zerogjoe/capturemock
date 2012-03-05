
""" Traffic classes for capturing client-server interaction """

import traffic, socket, sys, xmlrpclib

class ClientSocketTraffic(traffic.Traffic):
    destination = None
    direction = "<-"
    socketId = ""
    typeId = "CLI"
    def forwardToDestination(self):
        return self.forwardToServer() if self.destination is not None else []

    @classmethod
    def setServerLocation(cls, location):
        if cls.destination is None:
            cls.destination = location
            # If we get a server state message, switch the order around
            cls.direction = "->"
            ServerTraffic.direction = "<-"

    def forwardToServer(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(self.destination)
        sock.sendall(self.text)
        try:
            sock.shutdown(socket.SHUT_WR)
            response = sock.makefile().read()
            sock.close()
            return [ ServerTraffic(response, self.responseFile) ]
        except socket.error:
            sys.stderr.write("WARNING: Server process reset the connection while TextTest's 'fake client' was trying to read a response from it!\n")
            sock.close()
            return []


class XmlRpcClientTraffic(ClientSocketTraffic):
    def __init__(self, text="", responseFile=None, method=None, params=None):
        if method is not None:
            self.method = method
            self.params = params
            text = self.method + repr(params).replace(",)", ")")
        else:
            self.method, paramText = text.split("(", 1)
            self.params = eval("(" + paramText.rstrip())
        ClientSocketTraffic.__init__(self, text, None)
                
    def forwardToServer(self):
        try:
            response = getattr(self.destination, self.method)(*self.params)
            return [ XmlRpcServerTraffic(responseObject=response) ]
        except xmlrpclib.Fault, e:
            return [ XmlRpcServerTraffic(responseObject=e) ]


    def getXmlRpcResponse(self):
        return "" # not a response in the xmlrpc sense...


class ServerTraffic(traffic.Traffic):
    typeId = "SRV"
    direction = "->"


class XmlRpcServerTraffic(ServerTraffic):
    def __init__(self, text="", responseFile=None, responseObject=None):
        if responseObject is not None:
            self.responseObject = responseObject
            text = repr(responseObject)
            if isinstance(responseObject, xmlrpclib.Fault):
                text = "raise xmlrpclib.Fault(" + repr(responseObject.faultCode) + ", " + repr(responseObject.faultString) + ")"
        else:
            raiseException = text.startswith("raise ")
            if raiseException:
                text = text[6:]
            self.responseObject = eval(text)
        ServerTraffic.__init__(self, text, None)

    def getXmlRpcResponse(self):
        if isinstance(self.responseObject, xmlrpclib.Fault):
            raise self.responseObject
        else:
            return self.responseObject


class ServerStateTraffic(ServerTraffic):
    socketId = "SUT_SERVER"
    def __init__(self, inText, *args):
        ServerTraffic.__init__(self, inText, *args)
        lastWord = inText.strip().split()[-1]
        host, port = lastWord.split(":")
        ClientSocketTraffic.setServerLocation((host, int(port)))

    def forwardToDestination(self):
        return []

class XmlRpcServerStateTraffic(ServerTraffic):
    def __init__(self, dest, *args):
        ServerTraffic.__init__(self, "setServerLocation(<address>)", None)
        ClientSocketTraffic.setServerLocation(xmlrpclib.ServerProxy(dest))

    def forwardToDestination(self):
        return []
