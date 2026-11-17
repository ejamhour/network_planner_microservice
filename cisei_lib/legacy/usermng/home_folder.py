import os
from datetime import datetime
from pathlib import Path


def find_root_dir(nome_pasta, caminho_inicial='.'):
    caminho_atual = Path(caminho_inicial).resolve()

    while True:
        if caminho_atual.name == nome_pasta:
            return str(caminho_atual)  # Retorna como string (ou use return caminho_atual para manter como Path)
        
        if caminho_atual.parent == caminho_atual:
            break  # Chegou na raiz

        caminho_atual = caminho_atual.parent

    return None


class UserHome:

    def __init__(self, user_id , users_dir = '../home'):

        self.folders = {'upload', 'datasets', 'projects', 'configuration', 'monitoring', 'logs'}
        self.base_dir = Path(users_dir) / user_id
        self.base_dir = os.path.normpath(self.base_dir)
        
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, exc_traceback):
        print('UserHome: exiting...')
        return False

    # Create missing folders
    def create_folders(self):
        for folder in self.folders:
            path = Path(self.base_dir) / folder
            try:
                path.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                print(f'user_home: folder {folder} already exists')
    # Verify if user has all folders
    def check_folders(self):
        flag = True
        for folder in self.folders:
            path = Path(self.base_dir) / folder
            if not path.is_dir():
                print(f'user_home: folder {folder} is missing')
                flag = False
        return flag 
        


    # Get a file path in log folder
    def get_log_path(self, file_name):
        return str(Path(self.base_dir) / 'logs' / file_name)

    # Get a file path in upload folder
    def get_upload_path(self, file_name):
        return str(Path(self.base_dir) / 'upload' / file_name)

    # Get a file path in datasets folder
    def get_dataset_path(self, dataset, file_name=''):
        return str(Path(self.base_dir) / 'datasets' / dataset / file_name)

    # Get projects file path
    def get_project_path(self, project, file_name=''):
        return str(Path(self.base_dir) / 'projects' / project / file_name)

    # Get configuration path
    def get_configuration_path(self, file_name=''):
        return str(Path(self.base_dir) / 'configuration' / file_name)

    # Get monitoring path
    def get_monitoring_path(self, network, subdir='', filename='') -> Path:
        return Path(self.base_dir) / 'monitoring' / network / subdir/ filename  

    # Verify if a dataset exists
    def check_dataset(self, dataset):
        return (Path(self.base_dir) / 'datasets' / dataset).is_dir()

    # Clear log
    def clear_log(self, file):
        with open(self.get_log_path(file), 'w') as f:    
            f.write('')
    
    # Write log
    def write_log(self, file, message, to_console=False):
        now = datetime.now()
        formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")
        with open(self.get_log_path(file), 'a') as f:    
            f.write( formatted_time + ': ' + message + '\n')
        if to_console: print(message)


if __name__ == '__main__':   
    pass 