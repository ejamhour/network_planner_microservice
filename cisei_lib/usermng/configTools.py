import os
import tomlkit
from cisei_lib.cli.usermng.homeFolder import user_home
from shutil import copy2
from collections import defaultdict
import copy
from tomlkit import parse
from pathlib import Path

class configTools:

    def __init__(self, home : user_home, config_dir = 'configuration'):

        self.home = home
        self.log = 'configTools.log'
        self.config_dir = config_dir
        # self.home.clear_log(self.log)

    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, exc_traceback):
        if exc_type is not None:  # An exception occurred
            print(f"configTools terminated due to an exception: {exc_value}")
        return False # re-raises the exception (True to supress)
    
    # Get and convert TOML configuration
    def get_configuration_toml(self, file_name):
        if os.path.isfile(self.home.get_configuration_path(file_name)):
            with open(self.home.get_configuration_path(file_name), 'r') as f:
                return tomlkit.parse(f.read())
        else:
            return None

    # Load configuration
    def load_configuration(self, default_file, user_file = None ):

        self.default_config = self.get_configuration_toml(default_file)
        if self.default_config is None:
            try:
                dst = self.home.get_configuration_path(default_file)
                src = os.path.join(self.config_dir, default_file)
                copy2(src, dst)
                self.default_config = self.get_configuration_toml(default_file)
            except:
                raise RuntimeError('configTools: default configuration file is missing')

        if user_file:
            self.user_config = self.get_configuration_toml(user_file)
            if self.user_config is None:
                raise RuntimeError('configTools: user configuration file is missing')
        else:
            self.user_config = {}      

    
    # Load and merge multiple configurations
    def load_configs_from_dir(config_dir, config_type):
        merged_data = defaultdict(dict)
        last_meta = None

        for path in sorted(Path(config_dir).glob("[0-9][0-9]-*.toml")):
            try:
                content = path.read_text(encoding='utf-8')
                data = parse(content)
            except Exception as e:
                print(f"Skipping {path.name}: {e}")
                continue

            meta = data.get("meta")
            if not meta or meta.get("type") != config_type:
                continue

            last_meta = meta  # Keep the last meta for return

            # Merge *everything* except "meta"
            for key, value in data.items():
                if key == "meta":
                    continue
                if key not in merged_data:
                    # First time we see this section
                    merged_data[key] = copy.deepcopy(value)
                else:
                    # Merge dictionaries
                    if isinstance(value, dict):
                        merged_data[key].update(copy.deepcopy(value))
                    # Merge lists
                    elif isinstance(value, list):
                        if not isinstance(merged_data[key], list):
                            merged_data[key] = []
                        merged_data[key].extend(copy.deepcopy(value))
                    # For scalars, last file wins
                    else:
                        merged_data[key] = copy.deepcopy(value)

        return dict(merged_data), last_meta

        
            
    # Get parameter (#protected)
    def _get(self,  config : dict, path_key : str):

        keys = path_key.split('.')    
        
        for i,v in enumerate(keys):
            value = value.get(v, {})  if i > 0 else config.get(v, {})  

        return value        
    
    # Get parameter
    def get(self, path_key : str):

        if self.user_config is not None:
            value = self._get(self.user_config, path_key)        
            if value == {}:
                value = self._get(self.default_config, path_key)        
        else:
            value = self._get(self.default_config, path_key)        

        if value == {}:
            raise RuntimeError(f'configTools: invalid path key {path_key}')
        return value
    
    # Return a flatten dict with child keys
    def flatten(self, toml_dict, parent_key='', sep='.'):

        flat = {}
        for k, v in toml_dict.items():
            full_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                sub_flat = self.flatten(v, full_key, sep=sep)
                for sub_k, sub_v in sub_flat.items():
                    if sub_k in flat:
                        raise KeyError(f"Key conflict detected: {sub_k}")
                    flat[sub_k] = sub_v
            else:
                if full_key in flat:
                    raise KeyError(f"Key conflict detected: {full_key}")
                flat[full_key] = v
        return flat

class configRadio(configTools):

    def __init__(self, home : user_home, user_file = None, config_dir = 'configuration'):

        super().__init__(home, config_dir)
        self.log = 'configTools.log'

        super().load_configuration('radio_model.toml', user_file)


if __name__ == '__main__':   
    pass 