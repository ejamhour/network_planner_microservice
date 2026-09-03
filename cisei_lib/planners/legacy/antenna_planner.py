class AntennaPlanner():

    def __init__(self, project, home: user_home, **kwargs):  
        Serializer.__init__()

        self.home = home
        self.project = project    

        self.conf = configRadio(home, user_file=kwargs.get('config_file', None))
        self.rf = rfModel.rfModel(cover_height=self.conf.get('ENVIRONMENT.cover_height'))   

        DEFAULT_CONTEXT = {
            'radio_power' : self.conf.get('BACKHAUL.radio.power'),
            'tree_model' : self.conf.get('ENVIRONMENT.trees.tree_model'),
            'tree_delta' : self.conf.get('ENVIRONMENT.trees.tree_delta'),
            'level' : self.conf.get('ENVIRONMENT.trees.tree_level')
        }

        self.context = {**DEFAULT_CONTEXT, **kwargs}      
     
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):

        if exc_type:
            print(f"Exception: {exc_type}, {exc_val}")
        return False  # Return True if you want to suppress exceptions

    # Estimates link quality using the RF model. Can exclude antenna gains to support antenna planning.
    def set_path_nodes(self, nodes):
        self.nodes = nodes

    # Estimates link quality using the RF model. Can exclude antenna gains to support antenna planning.
    def link_quality(self, src_label, dst_label, ant=True):

        src_pos = self.nodes[src_label]['pos']
        dst_pos = self.nodes[dst_label]['pos']   
        
        ga1 = 0 if not ant else self.nodes[src_label].get('ant_dB', 0)
        ga2 = 0 if not ant else self.nodes[dst_label].get('ant_dB', 0)
   
        args = {
            'ha1' : self.nodes[src_label]['ant_h'],
            'ha2' : self.nodes[dst_label]['ant_h'],
            'ga1': ga1,
            'ga2': ga2,
            'pw' : self.conf.get('BACKHAUL.radio.power'),
            'tm' : self.conf.get('ENVIRONMENT.trees.tree_model'),
            'td' : self.conf.get('ENVIRONMENT.trees.tree_delta'),
            'level' : self.conf.get('ENVIRONMENT.trees.tree_level')
        }
        
        lqos = self.rf.link_quality(src_pos, dst_pos, **args)

        return lqos
    
    # Determines whether a new antenna can be assigned directly or requires B2B node insertion.
    def b2b_test(self, label, ant):
        # None (don't change), False (replace antenna), True (B2B)

        # node already has an omny antenna with higher gain --> do nothing
        cond0 = lambda label : 'ant_type' in self.nodes[label] and self.nodes[label]['ant_type'] == 'omni' and ant['type'] == 'omni' and self.nodes[label]['ant_dB'] >= ant['gain']
        # node has no antenna --> add new antenna to node 
        cond1 = lambda label : 'ant_type' not in self.nodes[label]
        # node has an omni antenna with a gain lower than the new omni antenna --> replace the old antenna 
        cond2 = lambda label : 'ant_type' in self.nodes[label] and self.nodes[label]['ant_type'] == 'omni' and ant['type'] == 'omni' and self.nodes[label]['ant_dB'] < ant['gain']
        # node has any type of antenna and the new antenna is directional --> add antenna to a new B2B radio and calculate azimute
        cond3 = lambda label: 'ant_type' in self.nodes[label] and ant['type'] == 'directional'
        # node has a directional antenna --> add antenna to a new B2B radio
        cond4 = lambda label: 'ant_type' in self.nodes[label] and self.nodes[label]['ant_type'] == 'directional'

        if cond0(label):
            return None
        if cond1(label) or cond2(label):
            return False    # no B2B required - assign antenna to the same node
        elif cond3(label) or cond4(label):
            return True

    # Selects and assigns antennas to meet RSSI target. Prefers omni antennas; uses directional and B2B only when required.
    def link_antennas(self, s_label, antennas = None):

        next = self.nodes[s_label]['next_hop']

        if next is None: 
            return False               

        if antennas is None:
            antennas = self.conf.get('BACKHAUL.antennas')       
        
        # calculate the link quality
        lqos = self.link_quality(s_label, next, False)
   
        # calculate required sum of the gain of both antennas
        ga_total = self.conf.get('BACKHAUL.radio.target_rssi') - lqos['rssi']
        ants = sorted(antennas, key = lambda x : x['gain']) 
        ga1 = ga2 = ants[0]

        for a in ants:
            ga1 = a # highest gain
            if ga1['gain'] + ga2['gain'] > ga_total:
                break
            ga2 = a
            if ga1['gain'] + ga2['gain'] > ga_total:
                break            

        # highest gain is for source node if antennas are omni, otherwise destination node 
        ant = {s_label : ga1,  next : ga2 } if ga1['type'] == 'ommi' else {s_label : ga2,  next : ga1 }
        self.nodes[s_label]['rssi'] = ga1['gain'] + ga2['gain'] + lqos['rssi'] 
        # OBS. received rssi assingned to the source node (simetric link)
        
        # - B2B nodes keep the same label followed by numbers 1, 2, 3, etc.
        par_map = list(zip(['ant_dB', 'ant_type', 'ant_name'], ['gain','type','name']))  
        
        # Destination node antenna selection
        b2bQ = self.b2b_test(next, ant[next])
        
        # already has a better antenna
        if b2bQ is not None:

            # new antenna in the same radio (does not change next_hop!)       
            if b2bQ is False:                    
                for a,b in par_map: self.nodes[next][a] = ant[next][b]               
                b2b_label_d = None
            # new antenna requires B2B - new node is downstream (b2b connection to the original node)
            if b2bQ is True: # this condition arises when connecting to an existent backhaul  (TODO: test this condition!)          
                # update the original node                
                if 'id_b2b' not in self.nodes[next]: self.nodes[next]['id_b2b'] = []
                b2b_label_d = f"{next}_{len(self.nodes[next]['id_b2b'])+1}" 
                self.nodes[next]['id_b2b'].append(b2b_label_d)     
                # create b2b node
                self.create_node(b2b_label_d, self.nodes[next]['pos'], next_hop=next, link_mode='b2b')                     
                for a,b in par_map: self.nodes[b2b_label_d][a] = ant[next][b] # receiver from downstream
                

        # Source node antenna selection
        b2bQ = self.b2b_test(s_label, ant[s_label])

        # already has a better antenna
        if b2bQ is not None:

            # new antenna in the same radio (check if next_hop was changed!)                      
            if b2bQ is False:        
                for a,b in par_map: self.nodes[s_label][a] = ant[s_label][b]   
                if b2b_label_d: self.nodes[s_label]['next_hop'] = b2b_label_d             
            # new antenna requires B2B - new node is upstream (RF connection to the next node)    
            if b2bQ is True:
                # update the original node
                if 'id_b2b' not in self.nodes[s_label]: self.nodes[s_label]['id_b2b'] = []                
                b2b_label_s = f"{s_label}_{len(self.nodes[s_label]['id_b2b'])+1}" 
                self.nodes[s_label]['id_b2b'].append(b2b_label_s) 
                self.nodes[s_label]['next_hop'] = b2b_label_s
                self.nodes[s_label]['link_mode'] = 'b2b'
                rssi = self.nodes[s_label].pop('rssi',None)
                # create b2b node
                next_hop = b2b_label_d if b2b_label_d is not None else next 
                self.create_node(b2b_label_s, self.nodes[s_label]['pos'], next_hop = next_hop)           
                for a,b in par_map: self.nodes[b2b_label_s][a] = ant[s_label][b] # transmitter to upstream 
                if rssi is not None: self.nodes[b2b_label_s]['rssi'] = rssi

    # Sets antennas for all segments in the current path.
    def path_antennas(self, antennas = None):
        # clear antennas and B2B nodes 

        node = self.s_label
        while node is not None:
            self.link_antennas(node, antennas)
            link_mode = self.nodes[node]['link_mode']
            node = self.nodes[node]['next_hop']
            if link_mode == 'b2b': 
                node = self.nodes[node]['next_hop']
                