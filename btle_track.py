#!/usr/bin/python

import os
import sys
import argparse
import csv
import traceback
import logging
import logging.handlers
from datetime import datetime
from datetime import timedelta
from urlparse import urlparse, parse_qs
import bisect
import threading
import BaseHTTPServer

import os.path
# Cat's mac address: e3:e2:e9:74:22:4b

from bluepy.btle import Scanner, DefaultDelegate

DATAPATH = '.'

#####################
# Web Server
#####################

class MyHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    def do_HEAD(s):
        s.send_response(200)
        s.send_header("Content-type", "text/plain")
        s.end_headers()

    def do_GET(s):
        """Respond to a GET request."""
        s.send_response(200)
        if s.path.startswith('/favicon'):
        	return
        args = parse_qs(urlparse(s.path).query)

        logging.info('HTTP request. Args: {}'.format(args))
        s.send_header("Content-type", "text/plain")
        s.end_headers()
        s.wfile.write('Hello Pussy Cat!')
    	s.wfile.write('\n')	

        mac = args.get('mac', None)
        when = args.get('when', None)
        threshold = args.get('threshold', [-80])
        period = args.get('period', [1])

        if mac and when:
        	mac = mac[0]
        	when = when[0]
        	try :
	        	when = datetime.strptime(when, '%Y%m%d%H%M%S')
	        except ValueError:
	        	logging.error('Bad datetime argument in URL QS: {}'.format(args))
	        	return

	        r = get_btle_data2(mac.lower(), when, int(threshold[0]), int(period[0]))
	        

	        for row in r:
	        	s.wfile.write(row)
	        	s.wfile.write('\n')	




class HTTPThread (threading.Thread):
    def __init__(self, port):
        threading.Thread.__init__(self)
        self.port = port
        self.daemon = True

    def run(self):
        logging.info("Starting HTTP Thread" )
        run_http(self.port)
        logging.info("Exiting HTTP Thread" )

def run_http(port):
    server_class = BaseHTTPServer.HTTPServer
    httpd = server_class(('', port), MyHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()


def find_imprint(ts):
	logging.debug('Finding imprint for ts = {}'.format(ts))
	keys = [r[0] for r in DATA]
	x = bisect.bisect_right(keys, ts)
	return x

def get_btle_data2(mac, when, threshold = -80, period = 10):
	# Find a 10 minute range of data. -10 to +10 minutes from the given timestamp.
	td = timedelta(minutes=period)
	start = find_imprint( unix_time(when - td) )
	end = find_imprint( unix_time(when + td) )
	logging.debug('Data search found range {} and {}. Threshold {}, period {}'.format(start, end, threshold, period))

	results = list()
	for i in range(start, end):
		(ts, addr, rssi) = DATA[i]
		if addr.lower() == mac and rssi > threshold:
			results.append( DATA[i] )

	logging.debug('Search found {} results: {}'.format(len(results), results))
	return results




def unix_time(dt):
	epoch = datetime.utcfromtimestamp(0)
	return (dt - epoch).total_seconds() 

def scanloop():
	scanner = Scanner()

	td = timedelta(days = 1)

	global DATA
	DATA = list()


	while True:
		snapts = datetime.now()
		datafile = os.path.join(DATAPATH, "btle_{}.csv".format(snapts.strftime("%Y%m%d")))

		logging.info("Writing scans to file {}".format(datafile))
		with open(datafile, 'ab', 0) as df:
			writer = csv.writer(df)

			while ( datetime.now() - snapts < td ) : 
				logging.info("Running scan...")
				devices = scanner.scan(1.0)
				for dev in devices:
					logging.info("Device %s (%s), RSSI=%d dB" % (dev.addr, dev.addrType, dev.rssi))
					ts = datetime.now()
					writer.writerow([dev.addr, dev.rssi, ts.isoformat()])

					DATA.append( (unix_time(ts), dev.addr, dev.rssi) )

				# Cleanup list by removing elements older than x minutes
				cleantd = timedelta(hours = 12)
				earlier = unix_time(datetime.now() - cleantd)
				i = -1
				for (ts, _, _) in DATA:
					if ts > earlier:
						break
					i = +1
				if i >= 0 :
					logging.info('Removing data older than {}, elements to delete: {}, size of data before {}'.format(cleantd, i, len(DATA)))
					DATA = DATA[i:]








def main():
	parser = argparse.ArgumentParser(description='BTLE Tracker')
	parser.add_argument('--log', help='Log file path', default='btle_track.log')
	parser.add_argument('--data', help='Path to write data files, in csv format.', default='.')
	parser.add_argument('--http_port', help='Port number of HTTP server', default=8080)


	args = parser.parse_args()

	logFormatter = logging.Formatter("%(asctime)s [%(levelname)-5.5s]  %(message)s")
	rootLogger = logging.getLogger()
	fileHandler = logging.handlers.RotatingFileHandler(args.log, maxBytes=(1024*1024*1024*5), backupCount=5)
	fileHandler.setFormatter(logFormatter)
	rootLogger.addHandler(fileHandler)

	consoleHandler = logging.StreamHandler(sys.stdout)
	consoleHandler.setFormatter(logFormatter)
	rootLogger.addHandler(consoleHandler)

	rootLogger.setLevel(logging.DEBUG)

	logging.info('BTLE Tracker started.')

	global DATAPATH
	DATAPATH = args.data

	t = HTTPThread(args.http_port)
	t.start()

	scanloop()
	logging.info('Stopping.')


if __name__ == '__main__':
	try:
		main()
	except KeyboardInterrupt as e:
		traceback.print_exc()
	except Exception as e:
		traceback.print_exc()     
	sys.exit(0)    