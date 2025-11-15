# PI_wineUIPC.py — dünner Wrapper im Klassen-Stil für XPPython3
import importlib

_impl = importlib.import_module("PythonPlugins.wineUIPC.main")

class PythonInterface:
    def XPluginStart(self):
        # gibt (name, sig, desc) zurück
        return _impl.XPluginStart()

    def XPluginStop(self):
        _impl.XPluginStop()

    def XPluginEnable(self):
        return _impl.XPluginEnable()

    def XPluginDisable(self):
        _impl.XPluginDisable()

    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
        # optional: falls du später Messages brauchst
        try:
            _impl.XPluginReceiveMessage(inFromWho, inMessage, inParam)  # existiert bei dir nicht – einfach ignorieren
        except AttributeError:
            pass
