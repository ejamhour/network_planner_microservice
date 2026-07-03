import pickle
import tomlkit
import msgpack
import tomlkit

class SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        # Allow only safe, built-in classes or tomlkit elements
        safe_classes = ("int", "str", "float", "list", "dict", "tuple")

        # This will allow any class from tomlkit.* to be unpickled
        # Modify the logic if you want to be more specific
        tomlkit_modules = ["tomlkit.items", "tomlkit.document", "tomlkit"]

        # Allow built-in types and tomlkit-related modules
        if module == "builtins" and name in safe_classes:
            return super().find_class(module, name)

        if module.startswith("tomlkit") and module in tomlkit_modules:
            return super().find_class(module, name)

        raise pickle.UnpicklingError(f"Unsafe class attempt: {module}.{name}")   


# Custom function to encode tomlkit elements recursively
def tomlkit_encoder(obj):
    if isinstance(obj, tomlkit.items.String):
        return {"__tomlkit.String__": str(obj)}
    if isinstance(obj, tomlkit.items.Integer):
        return {"__tomlkit.Integer__": int(obj)}
    if isinstance(obj, tomlkit.items.Float):
        return {"__tomlkit.Float__": float(obj)}
    if isinstance(obj, tomlkit.items.Table) or isinstance(obj, dict):
        return {"__tomlkit.Table__": {k: tomlkit_encoder(v) for k, v in obj.items()}}
    if isinstance(obj, list):  # Ensure lists are processed correctly
        return [tomlkit_encoder(v) for v in obj]
    return obj  # Default case

# Custom function to decode tomlkit elements recursively
def tomlkit_decoder(obj):
    if "__tomlkit.String__" in obj:
        return tomlkit.items.String(obj["__tomlkit.String__"])
    if "__tomlkit.Integer__" in obj:
        return tomlkit.items.Integer(obj["__tomlkit.Integer__"])
    if "__tomlkit.Float__" in obj:
        return tomlkit.items.Float(obj["__tomlkit.Float__"])
    if "__tomlkit.Table__" in obj:
        return {k: tomlkit_decoder(v) for k, v in obj["__tomlkit.Table__"].items()}
    if isinstance(obj, list):  # Ensure lists are processed correctly
        return [tomlkit_decoder(v) for v in obj]
    return obj  # Default case

# Find keys that cannot be saved with message pack
def find_unhashable_keys(d, path='root'):
    print(f'testing {d} and {path}')
    if isinstance(d, dict):
        for k, v in d.items():
            if not isinstance(k, (str, int, float, bool)):
                print(f"❌ Unhashable key at {path}: {repr(k)} ({type(k)})")
            elif isinstance(k, tuple):
                for i, part in enumerate(k):
                    if not isinstance(part, (str, int, float, bool)):
                        print(f"❌ Unhashable key part in tuple at {path}: {repr(part)} ({type(part)})")
            find_unhashable_keys(v, f"{path}[{repr(k)}]")
    elif isinstance(d, (list, tuple)):
        for i, item in enumerate(d):
            find_unhashable_keys(item, f"{path}[{i}]")

if __name__ == '__main__':

    with open("data.pkl", "rb") as f:
        safe_unpickler = SafeUnpickler(f)
        data = safe_unpickler.load()

    doc = tomlkit.document()
    doc["title"] = tomlkit.items.String("Example")
    doc["count"] = tomlkit.items.Integer(42)
    doc["settings"] = tomlkit.table()
    doc["settings"]["debug"] = tomlkit.items.Float(3.14)
    doc["nested"] = {"level1": {"level2": {"key": tomlkit.items.String("deep")}}}

    # Serialize with msgpack
    packed = msgpack.packb(doc, default=tomlkit_encoder)

    # Deserialize with msgpack
    unpacked = msgpack.unpackb(packed, object_hook=tomlkit_decoder)