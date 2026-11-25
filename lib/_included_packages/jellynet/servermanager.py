"""
JellyfinServerManager: what plexapp.SERVERMANAGER was for Plex, for the Jellyfin servers JM4K knows.
Jellyfin has no plex.tv discovery, reachability polling or Discover service, so those calls do nothing.
"""

from plexnet import signalsmixin


class JellyfinServerManager(signalsmixin.SignalsMixin):
    def __init__(self, servers):
        signalsmixin.SignalsMixin.__init__(self)
        self.servers = list(servers)
        self.selectedServer = self.servers[0] if self.servers else None

    @property
    def serversByUuid(self):
        return {s.uuid: s for s in self.servers}

    @property
    def connectedServers(self):
        return list(self.servers)

    def getServers(self):
        return list(self.servers)

    def getServer(self, uuid):
        return self.serversByUuid.get(uuid)

    def serversKnown(self):
        return True

    def getDiscoverServer(self):
        return None

    def getWatchlistServer(self):
        return None

    def periodicReachabilityCheck(self, *args, **kwargs):
        pass

    def checkServersAlive(self, *args, **kwargs):
        pass

    def retestServerNow(self, *args, **kwargs):
        return False

    def setWatchedServers(self, *args, **kwargs):
        pass

    def cancelOfflineRetry(self):
        pass

    def resumeOfflineRetry(self):
        pass
