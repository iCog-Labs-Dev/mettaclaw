import pathlib
import yaml
import importlib
import importlib.util
import pluginapi
import sys

_REPO = pathlib.Path(__file__).parent.parent.resolve()
_plugins = {}
_commchannel: pluginapi.CommChannel = None

def error(func, text):
    error = f"{func}: {text}"
    print(error)
    raise RuntimeError(error)

def initPlugins():
    global _plugins, _REPO

    plugins_path = _REPO.joinpath("./config/plugins.yaml")
    with open(plugins_path, "r") as f:
        plugins = yaml.safe_load(f)

    for i, p in enumerate(plugins):

        name = p.get("name")
        if name is None:
            error("initPlugins", f"name field is empty, file: {plugins_path}, index: {i}")
        if name in _plugins:
            error("initPlugins", f"name '{name}' is not unique")

        loader = p.get("loader", "metta")
        if loader == "python":
            initPythonPlugin(p)
        elif loader == "metta":
            initMettaPlugin(p)
        else:
            error("initPlugins", f"loader '{loader}' is not implemented")

class PythonPlugin:

    def __init__(self, mod):
        self.mod = mod

def initPythonPlugin(plugin):
    global _plugins, _REPO

    name = plugin.get("name")
    location = plugin.get("location")
    mod = None
    if location is None:
        print(f"initPythonPlugin: loading {name} plugin from PYTHONPATH using Python module loader")
        mod = importlib.import_module(name)
    else:
        location = pathlib.Path(location.format(REPO=_REPO)).resolve()
        modpath = location.joinpath(f"{name}.py").resolve()
        print(f"_initPythonPlugin: loading {name} plugin from {modpath} using Python module loader")
        # adding location into sys.path to be able loading plugins which
        # consist of multiple files
        sys.path.append(str(location))
        spec = importlib.util.spec_from_file_location(name, modpath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

    if mod is not None:
        _plugins[name] = PythonPlugin(mod)
    else:
        error("initPythonPlugin", f"Couldn't find Python module {name}")

    if not hasattr(mod, "loadOmegaClawPlugin"):
        error("initPythonPlugin", f"No loadOmegaClawPlugin() function is implemented by plugin {name}")
    plugin_loader = getattr(mod, "loadOmegaClawPlugin")
    plugin_loader()

def initMettaPlugin(plugin):
    raise NotImplementedError()

def listToDict(list):
    print(f"listToDict: list {list}")
    dict = {}
    list = [s.split("=", 1) for s in list]
    for k, v in list:
        dict[k] = v
    return dict

def commChannelConfig(commchannel, config):
    global _commchannel
    _commchannel = pluginapi._commChannelRegistry.get(commchannel, None)
    if _commchannel is None:
        error("commChannelConfig", f"Communication channel plugin {commchannel} is not registered")
    _commchannel.config(config)

def commChannelReceive():
    global _commchannel
    return _commchannel.receive()

def commChannelSend(message):
    global _commchannel
    _commchannel.send(message)
