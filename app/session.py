from cisei_lib.core.rf.rf_engine import RFEngine


class UserRuntime:
    def __init__(self):
        self.rfo = RFEngine()
        self.link = None
        self.filtered_df = None
        self.invasions = None
        self.bldg_zones = None
        self.bldg_browser = None
        self.step = 0
        self.history = []

    def link_change(self, link):
        self.link = link
        self.rfo.gio.building_df = None
        self.filtered_df = None
        self.invasions = None
        self.bldg_zones = None
        self.bldg_browser = None
        self.step = 1
        self.history = ['set_link']

    def bldg_prepare(self):
        self.filtered_df = None
        self.invasions = None
        self.bldg_zones = None
        self.bldg_browser = None
        self.step = 3
        self.history.append('bldg_prepare')

    def bldg_filter(self, filtered_df):
        self.filtered_df = filtered_df
        self.invasions = None
        self.bldg_zones = None
        self.bldg_browser = None
        self.step = 4
        self.history.append('filtered_df')

    def bldg_invasions(self, zones, invasions):
        self.invasions = invasions
        self.bldg_zones = zones
        self.bldg_browser = None
        self.step = 5
        self.history.append('bldg_invasions')

    def bldg_set_browser(self, browser):
        self.bldg_browser = browser
        self.step = 6
        self.history.append('bldg_browser')

    


        
