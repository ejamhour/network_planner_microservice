import json
import os
import importlib
from pathlib import Path

# Check minio
from tests.test_minio import test_minio_auth_basic

# --- your existing imports ---
import cisei_lib.dem.dem_utils as du
import cisei_lib.core.profiles.geo_info_vector as giv
import cisei_lib.core.rf.rf_engine as rf
from cisei_lib.core.profiles.geo_info import DSFlags, P2PLink

import sys
import logging
import traceback
from geopy.distance import distance

'''
logging.basicConfig(
	level=logging.DEBUG,
	format="%(levelname)s:%(name)s:%(filename)s:%(lineno)d: %(message)s"
)
'''

def global_exception_handler(exc_type, exc_value, exc_traceback):
	logging.error(
		"UNCAUGHT EXCEPTION",
		exc_info=(exc_type, exc_value, exc_traceback)
	)



# --- reloads (as you already do) ---
importlib.reload(du)
importlib.reload(giv)
importlib.reload(rf)

# Store your found thresholds in a config dictionary
RADIO_CONFIG = {
	'125':   {'sens': -105, 'pristine': 8,  'usable': 14},
	'250':   {'sens': -103, 'pristine': 16, 'usable': 21},
	'500':   {'sens': -99,  'pristine': 8,  'usable': 14},
	'1000N': {'sens': -92,  'pristine': 4,  'usable': 6}
}


# --------------------------------------------------
# Your existing function (UNCHANGED)
# --------------------------------------------------
def eval_link(dataset, rfo, lqi_range=(None, None), freq=900):

	def is_in_range(val, bounds):
		if val is None:
			return False
		low, high = bounds
		return (low is None or val >= low) and (high is None or val <= high)

	for idx, data in enumerate(dataset):

		lqi = data.get("rx_lqi")
		rssi = data.get("rx_rssi")
		snr = data.get("rx_snr")
		link_name = idx

		if is_in_range(lqi, lqi_range):

			tx = (data.get("tx_lat"), data.get("tx_lon"))
			rx = (data.get("rx_lat"), data.get("rx_lon"))

			if tx is None or rx is None:
				continue

			D = distance(tx,rx).meters

			if  D < 30:
				continue			

			tx_ha = float(data.get("tx_ant_height", 7))
			rx_ha = float(data.get("rx_ant_height", 7))
			tx_ha = max(tx_ha, 7)
			rx_ha = max(rx_ha, 7)

			link = P2PLink(tx, rx, tx_ha, rx_ha, freq)
			save_processed_link('last_processed.txt', link)

			try:
				rfo.load_profile(link)
				res_link = rfo.evaluate_link()

				res = {
					"link_name": link_name,
					"performance": {
						"lqi": lqi,
						"rssi": rssi,
						"snr": snr,
					},
					"antennas": {
						"tx": data.get("tx_ant_type", "N/A"),
						"rx": data.get("rx_ant_type", "N/A"),
					},
				}

				res.update( res_link )
				res.update({'source_data' : data })

				yield rf.clean_dict(res), link, rfo.filtered_df

			except Exception:
				traceback.print_exc()
				print({"link_name": link_name}, link)
				exit(1)
				yield {"link_name": link_name, "error": [link.tx, link.rx ] }, link, None


# --------------------------------------------------
# Batch consumer
# --------------------------------------------------
def consume_in_batches(generator, batch_size):
	batch = []
	for item in generator:		
		batch.append(item)
		if len(batch) >= batch_size:
			yield batch
			batch = []
	if batch:
		yield batch


# --------------------------------------------------
# Safe batch writer (append-only)
# --------------------------------------------------
def save_batch(batch, out_file):
	out_file = Path(out_file)
	out_file.parent.mkdir(parents=True, exist_ok=True)

	with out_file.open("a", encoding="utf-8") as f:
		for res, _, _ in batch:
			f.write(json.dumps(res) + "\n")
			f.flush()


# --------------------------------------------------
# Checkpoint helpers
# --------------------------------------------------
def load_checkpoint(path):
	if not os.path.exists(path):
		return 0
	with open(path, "r") as f:
		return int(f.read().strip())


def save_checkpoint(path, idx):
	with open(path, "w") as f:
		f.write(str(idx))

def save_processed_link(path, link):
	with open(path, "w") as f:
		f.write(str(link))

# --------------------------------------------------
# Main execution
# --------------------------------------------------
def run(dataset,
		out_file="results.jsonl",
		checkpoint_file="checkpoint.txt",
		batch_size=100,
		freq=900):
	
	rfo = rf.RFEngine()

	start_idx = load_checkpoint(checkpoint_file)
	print(f"Starting from index {start_idx}")
	end_idx = start_idx + batch_size
	target_data = dataset[start_idx:end_idx]
	
	if not target_data:
		print("No more data to process.")
		return

	# This is a link generator (stop at yield)
	gen = eval_link(target_data, rfo=rfo, freq=freq)

	for batch in consume_in_batches(gen, batch_size):

		save_batch(batch, out_file)

		# last processed link index
		new_checkpoint = start_idx + len(batch)
		save_checkpoint(checkpoint_file, new_checkpoint)

		save_checkpoint(checkpoint_file, new_checkpoint)

		print(f"Saved batch. Checkpoint at {new_checkpoint}")


# --------------------------------------------------
# Example call
# --------------------------------------------------
if __name__ == "__main__":

	sys.excepthook = global_exception_handler

	# dataset must already be loaded as a list of dicts
	# dataset = [...]

	os.environ["MINIO_ENDPOINT"] = "http://127.0.0.1:9000"
	print(os.environ["MINIO_ENDPOINT"])
	os.environ["MINIO_DEM_ROOT_KEY"] = "root/dem-datasets/"
	print(os.environ["MINIO_DEM_ROOT_KEY"])

	test_minio_auth_basic()

	current_dir = Path.cwd()
	print(f"Current Directory: {current_dir}")
	test_file = current_dir / 'data' / 'links.json'

	if test_file.exists() and test_file.is_file():
		with open(test_file, 'r', encoding='utf-8') as f:
			dataset = json.load(f)
			dataset = [d for d in dataset if d['rx_lqi'] > 0 ]
			print(f"Successfully loaded: {test_file.name}")
	else:
		print(f"Error: The file at {test_file} does not exist.")



	run(
		dataset=dataset,
		out_file="results.jsonl",
		checkpoint_file="checkpoint.txt",
		batch_size=100,
		freq=900,
	)
