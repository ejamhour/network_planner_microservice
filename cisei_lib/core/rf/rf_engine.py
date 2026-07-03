"""
RFEngine: orchestrates path-loss, diffraction, and feature extraction.
Holds only *state* for a single link computation.
All RF physics lives in diffraction.py, propagation.py, features.py.
"""

import numpy as np
from importlib import resources
import json
import cisei_lib
from cisei_lib.core.profiles.geo_info_vector import geoInfoVector
from cisei_lib.core.profiles.geo_info import DSFlags, P2PLink
from cisei_lib.core.rf.propagation import fspl       
from cisei_lib.core.rf.diffraction import delta_bullington, deygout_three_peak
from cisei_lib.core.rf.rssi_core import ExpressionModel as rssi_model
from cisei_lib.core.rf.link_configuration import LinkConfig
from geopy.distance import distance


import logging
logger = logging.getLogger(__name__)


class RFEngine:
    """
    RF path-loss and diffraction engine.
    -------------
    geo     : GeoInfoVector instance
    """

    def __init__(self, **kwargs):

        # GeoInfo object
        self.gio = geoInfoVector(**kwargs)

        # Other data
        self.diffra_method = kwargs.get('diffraction_method', 'delta_diffra')

        self.link = None
        self.features = None
        self.filtered_df = None

        self.rssi_model = self.load_rssi_model(
            custom_model=kwargs.get('custom_model'), 
            default_model=kwargs.get('default_model', 'default_900'))

 
    # ---------------------------------------------------------------
    # Load coefficents model
    # ---------------------------------------------------------------
    def load_rssi_model(self, custom_model : dict = None, default_model = 'default_900'):
        if custom_model:
            self.rssi_model = custom_model
        else:
            resource_path = resources.files(cisei_lib).joinpath(f'resources/rssi_models/{default_model}.json')
            self.rssi_model = json.loads(resource_path.read_text(encoding='utf-8'))
        
        return self.rssi_model
    

    # ---------------------------------------------------------------
    # Load terrain + cover profile
    # ---------------------------------------------------------------
    def load_profile(self, link: P2PLink):
        """
        ininitialize dicts and 
        """
        self.gio.initialize_2D_dictionaries(link) # DTM, DSM and LULC
        self.gio.create_link_dataframe(link) # BLDG
        self.link = link
        self.features = None


    # ---------------------------------------------------------------
    # Link Evaluation (automatically load)
    # ---------------------------------------------------------------

    def evaluate_link(self, link: P2PLink | None = None):
        # 1. Sync the link state
        if link is not None and link != self.link:
            self.load_profile(link)
        
        if self.link is None:
            raise RuntimeError("No link loaded. Pass a link or call load_profile first.")

        # 2. Collect components
        # Assuming self.path_loss() returns a dictionary
        results = self.path_loss() 

        # 3. Add diffraction using the specific method name as the key
        results[self.diffra_method] = self.diffraction_loss()

        # 4. Merge invasions and features
        # This syntax (|=) merges dictionaries in place (Python 3.9+)
        results |= self.clutter_invasions()
        results |= self.other_features()

        self.features = clean_dict(results)
        
        return self.features

    # ---------------------------------------------------------------
    # Invasions e other link features
    # ---------------------------------------------------------------

    def clutter_invasions(self):

        if self.link is None:
            raise RuntimeError('link must be initialized with load profile')
        
        res_t, res_c = self.gio.fresnel_radial_invasions(self.link)

        res_b = {'core': 0, 'fresnel': 0, 'boundary': 0}

        if self.gio.building_df is not None and not self.gio.building_df.empty:
            filtered_df = self.gio.filter_buildings_by_fresnel() # Obs. gio has a copy of link if there are buildings
            if filtered_df is not None and not filtered_df.empty:
                res_b, _ = self.gio.buildings_radial_invasions(filtered_df)
                self.filtered_df = filtered_df

        invasions = {
            'terrain' : res_t,
            'vegetation' : res_c,
            'buildings' : res_b
        }

        return invasions

    def other_features(self):
        if self.link is None:
            raise RuntimeError('link must be initialized with load profile')

        extra_features = self.gio.extract_link_features(self.link)

        return extra_features

    # ---------------------------------------------------------------
    # Diffraction
    # ---------------------------------------------------------------
    def diffraction_loss(self):
        """
        Compute diffraction loss for the loaded profile.
        method: 'delta' (default) or 'deygout'
        """
        if self.link is None:
            raise RuntimeError('link must be initialized with load profile')
       
        link = self.link
         
        D = distance(link.tx, link.rx).meters
        native = sorted(
            float(d)
            for d in self.gio.dtm_dict.keys()
            if 0.0 < float(d) < D
        )

        distances = np.array([0.0] + native + [float(D)], dtype=float)

        dtm_h = self.gio.sample_dict(
            distances,
            self.gio.dtm_dict,
            None,
            mode="bilinear"
        )
        
  
        if self.diffra_method == "delta_diffra":            
            difra = delta_bullington(dtm_h, distances, link.tx_ha, link.rx_ha, link.freq_mhz)

        elif self.diffra_method == "deygout_diffra":            
            difra = deygout_three_peak(dtm_h, distances, link.tx_ha, link.rx_ha, link.freq_mhz)

        else:
            raise ValueError(f"Unknown diffraction method '{self.method}'")
        
        return difra

    # ---------------------------------------------------------------
    # Path Loss (FSPL / Two-Ray)
    # ---------------------------------------------------------------
    def path_loss(self, link: P2PLink | None = None):
        """
        Compute free-space or two-ray ground reflection loss.
        (Placeholder: implement later)
        """
        if link is None:
            link = self.link
        if self.link is None:
            raise RuntimeError('link must be initialized with load profile')

        D = distance(link.tx, link.rx).meters
        wavelength = 299.792458 / self.link.freq_mhz

        return {'fspl' : fspl(D, wavelength), 'dist_m' : D }

    # ---------------------------------------------------------------
    # Vegetation Loss (apply coefficient to clutter)
    # ---------------------------------------------------------------
    def vegetation_loss(self, coef = None):
        """
        Compute vegetation attenuation using analytical model.
        """
        if self.features is None:
            raise RuntimeError('link features is not available')
        
        if coef is None:
            coef = (0.39, 0.39, 0.25)
        
        return self._clutter_to_attenuation(self.features['vegetation'], coef=coef)

    # ---------------------------------------------------------------
    # Building Loss (apply coefficient to clutter)
    # ---------------------------------------------------------------
    def building_loss(self, coef = None):
        """
        Compute building attenuation using analytical model.
        """
        if self.features is None:
            raise RuntimeError('link features is not available')
    
        if coef is None:
            coef = (0.39, 0.39, 0.25)

        return self._clutter_to_attenuation(self.features['buildings'], coef=coef)

    # ---------------------------------------------------------------
    # Total Loss
    # ---------------------------------------------------------------
    def total_loss(self):
        """
        Combine path loss + diffraction + vegetation.
        """
        if self.features is None:
            raise RuntimeError('link features is not available')    
        
        d_loss = self.features['delta_diffra']
        p_loss = self.features['fspl']
        v_loss = self.vegetation_loss()
        b_loss = self.building_loss()
        return sum([ d_loss, p_loss, v_loss, b_loss ] )

    # ---------------------------------------------------------------
    # RSSI - using standard analytical functions
    # ---------------------------------------------------------------
    def compute_rssi(self, l: LinkConfig):
        """
        Compute RSSI from total loss and radio parameters.
        """
        total = self.total_loss()
        return l.tx.pw + l.tx.ant_gain + l.rx.ant_gain - total

    #----------------------------------------------------------------
    # RSSI - using the trained model
    #----------------------------------------------------------------
    def predict_rssi(self, l: LinkConfig):

        if self.features is None:
            raise RuntimeError('link features is not available')

        # 1. Fallback to instance attributes


       
        model = rssi_model.from_dict(self.rssi_model)
        record = l.to_dict()
        exp = model.explain(record)    
        
        return exp

#---------------------------------------
# Private Methods

    def _clutter_to_attenuation(self, zones, coef, radial_weights = None):
        """
        Calculates attenuation using a Radial Deepness variation of the FITU-R model.
        Redefines 'd' as a weighted sum of radial zone invasions.
        """
        # Radial Deepness Weights (1 - r)
        # Default assumes core as full light
        default_weights = {
            'core': 1,      # r ~ 0.15
            'fresnel': 0,   # r ~ 0.57
            'boundary': 0   # r ~ 0.92
        }
        
        weights = radial_weights if radial_weights else default_weights

        # Default to FITU-R 'trees with leaves' if no coefficients are provided
        A, B, C = coef 

        f_ghz = self.link.freq_mhz / 1e3
        
        # 1. Calculate Weighted Effective Depth (d_eff)
        # Using .get() ensures it won't crash if a zone is missing from the dict
        d_eff = (zones.get('core', 0) * weights['core'] + 
                zones.get('fresnel', 0) * weights['fresnel'] + 
                zones.get('boundary', 0) * weights['boundary'])
        
        if d_eff <= 0:
            return 0.0
            
        # 2. Apply the modified Power Law: A * f^B * d_eff^C
        attenuation = A * (f_ghz**B) * (d_eff**C)
        
        return attenuation


#--------------------------------------
# Auxiliar methods outside the class

def clean_dict(d):
    """Recursively converts numpy types and rounds floats for readability."""
    if isinstance(d, dict):
        return {k: clean_dict(v) for k, v in d.items()}
    elif isinstance(d, (list, tuple)):
        return [clean_dict(x) for x in d]
    elif isinstance(d, (float, np.float64, np.float32)):
        # If it's a whole number (like -53.0), make it an int (-53)
        if d.is_integer():
            return int(d)
        # Otherwise round to 2 decimals
        return round(float(d), 2)
    return d

