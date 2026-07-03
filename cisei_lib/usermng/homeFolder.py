import os
from datetime import datetime


def find_root_dir(nome_pasta, caminho_inicial='.'):
    # Obtém o caminho absoluto do diretório inicial
    caminho_atual = os.path.abspath(caminho_inicial)

    while True:
        # Verifica se o nome da pasta atual corresponde ao nome desejado
        if os.path.basename(caminho_atual) == nome_pasta:
            return caminho_atual  # Retorna o caminho da pasta encontrada
        
        # Move para o diretório pai
        caminho_anterior = caminho_atual
        caminho_atual = os.path.dirname(caminho_atual)
        
        # Se já estiver no diretório raiz, saia do loop
        if caminho_atual == caminho_anterior or not caminho_atual:
            break

    return None  # Retorna None se não encontrar a pasta

class user_home:

    def __init__(self, user_id , users_dir = '../home'):

        self.folders = {'upload', 'datasets', 'projects', 'configuration', 'perfomance', 'logs'}
        self.base_dir = os.path.join(users_dir, user_id)
        self.base_dir = os.path.normpath(self.base_dir)
        
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, exc_traceback):
        print('userHome: exiting...')
        return False
     
    def create_folders(self):
        for folder in self.folders:
            try:
                os.makedirs(os.path.join(self.base_dir,folder))                 
            except:
                print('user_home: folder {folder} already exists')
    
    def check_folders(self):
        flag = True
        for folder in self.folders:
            if not os.path.isdir(os.path.join(self.base_dir,folder)):
                print(f'user_home: folder {folder} is missing')
                flag = False
        return flag               
    
    # Get a file path in log folder
    def get_log_path(self, file_name):
        return os.path.normpath(os.path.join(self.base_dir, 'logs' , file_name))

    # Get a file path in upload folder
    def get_upload_path(self, file_name):
        return os.path.normpath(os.path.join(self.base_dir, 'upload' , file_name))

    # Get a file path in datasets folder
    def get_dataset_path(self, dataset, file_name=''):
        return os.path.normpath(os.path.join(self.base_dir, 'datasets' , dataset, file_name))
    
    # Get projects file path
    def get_project_path(self, project, file_name):
        return os.path.normpath(os.path.join(self.base_dir, 'projects' , project, file_name))
    
    # Get configuration path
    def get_configuration_path(self, file_name):
        return os.path.normpath(os.path.join(self.base_dir, 'configuration' , file_name))   
    
    # Get performance path
    def get_performance_path(self, project, file_name):
        return os.path.normpath(os.path.join(self.base_dir, 'performance' , file_name))
    
    # Vefify if a dataset exists
    def check_dataset(self, dataset):
        return os.path.isdir(self.get_dataset_path(dataset, ''))

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