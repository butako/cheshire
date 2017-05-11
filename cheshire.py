#!/usr/bin/python

import RPi.GPIO as GPIO
import traceback
import sys
import time
import urllib, urllib2
import smtplib
from os.path import basename
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import COMMASPACE, formatdate
import logging
import logging.handlers
from collections import deque
import os
import itertools
import argparse
import threading
import pygame
import exifread
import BaseHTTPServer



IMAGES = deque()
EVENTS = deque()
ARGS = None
DEBOUNCE_TIMER = None
BUZZER = 18
LED_RED = 27
LED_GREEN = 22
LED_YELLOW = 17

class CamImage:
	def __init__(self, filename):
		self.filename = filename
		self.isEvent = False
	def __str__(self):
		return self.filename
	def __repr__(self):
		return self.filename

class Event:
	def __init__(self, images):
		for i in images:
			i.isEvent = True
		self.images = images

	def unlink(self):
		try:
			for i in self.images:
				os.unlink(i.filename)
		except:
			pass


def send_mail(send_from, send_to, subject, text, files=None,
              server="127.0.0.1"):
    assert isinstance(send_to, list)

    msg = MIMEMultipart()
    msg['From'] = send_from
    msg['To'] = COMMASPACE.join(send_to)
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = subject

    msg.attach(MIMEText(text))

    for f in files or []:
        with open(f, "rb") as fil:
            part = MIMEApplication(
                fil.read(),
                Name=basename(f)
            )
            part['Content-Disposition'] = 'attachment; filename="%s"' % basename(f)
            msg.attach(part)


    smtp = smtplib.SMTP(server)
    smtp.sendmail(send_from, send_to, msg.as_string())
    smtp.close()



def takePhoto2(catcam):
	logging.debug("Taking photo...")
	before = datetime.now()
	b = catcam.readline()
	if not b.startswith('--'):
		logging.error("Expected boundary string, got {}".format(b))
		return False
	ct = catcam.readline()
	cls = catcam.readline()
	if not cls.startswith('Content-Length'):
		logging.error("Expected content length, got {}".format(cls))
		return False

	cl = [int(s) for s in cls.split() if s.isdigit()][0]
	catcam.readline()
	imgdata = catcam.read(cl)
	# read newline after data, leaving us ready for next boundary line :
	catcam.readline() 

	timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
	filename = os.path.join(ARGS.output,"catflap_{}.jpg".format(timestamp))

	with open(filename,'wb') as imgfile:
		imgfile.write(imgdata)

	global IMAGES
	IMAGES.appendleft(CamImage(filename))
	if len(IMAGES) >= 500:
		r = IMAGES.pop()
		try:
			if not r.isEvent:
				os.unlink(r.filename)
		except:
			pass

	after = datetime.now()
	delta = after - before
	logging.info("Photo taken in {} seconds, {} ".format(delta.total_seconds(), filename))
	return True


def applyMotionFilter(imgs, threshold):
	# Motion Filter
	# check for motion data in the ImageDescription exif tag and
	# remove any images which do not contain enough motion
	f_imgs = list()
	for i in imgs:
		with open(i.filename,'rb') as f:
			exiftags = exifread.process_file(f, details=False, stop_tag='ImageDescription')
			logging.debug("EXIF extracted: {}".format(exiftags))
			if "Image ImageDescription" in exiftags:
				motion = int(exiftags["Image ImageDescription"].values)
				logging.info("EXIF Motion value {}, threshold {}.".format(motion, threshold))
				if motion >= threshold:
					f_imgs.append(i)
	return f_imgs




def saveEvent(images):
	ev = Event(images)
	global EVENTS
	EVENTS.appendleft(ev)
	if len(EVENTS) >= 5:
		r = EVENTS.pop()
		r.unlink()



def onCatFlapTriggered():
	global IMAGES
	logging.info("Cat flap actually triggered...")

	imgs = applyMotionFilter(list(itertools.islice(IMAGES, 0, 10)), ARGS.motion)

	if len(imgs) == 0:
		logging.info("No images to send after motion filter applied. False alarm folks!")
		return

	#Save the images for the web server
	saveEvent(imgs)

	logging.info("Sending photos: {}".format(imgs))
	timestamp = datetime.now().strftime('%H:%M:%S')
	subject = "{} {}".format("ROSIE Alert", timestamp)
	
	send_mail(ARGS.mail_from, ARGS.mail_to, 
			subject, 'Cat flap triggered',
			[i.filename for i in imgs], ARGS.mail_smtp) 



def onCatFlapTriggered_debouncer(channel):
	logging.info("Cat flap triggered!")
	global DEBOUNCE_TIMER
	if DEBOUNCE_TIMER:
		logging.info("Bounce debounced.")
		DEBOUNCE_TIMER.cancel()
	
	DEBOUNCE_TIMER = threading.Timer(5, onCatFlapTriggered)
	DEBOUNCE_TIMER.start()
	pygame.mixer.music.play()
	for i in range(5):
		GPIO.output(BUZZER, GPIO.HIGH)
		time.sleep(0.1)
		GPIO.output(BUZZER, GPIO.LOW)
		time.sleep(0.1)


def ledLoop():
	states = deque([True,False,False])
	d = 1 
	while True:
		GPIO.output(LED_GREEN, states[0])
		GPIO.output(LED_YELLOW, states[1])
		GPIO.output(LED_RED, states[2])
		states.rotate(d)
		if states[2]:
			d = -1
		if states[0]:
			d = 1
		logging.debug(states)
		time.sleep(0.5)




####################
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

        logging.info('HTTP request {}'.format(s.path))

        if s.path.startswith('/trigger'):
        	onCatFlapTriggered_debouncer(0)
        	s.send_header("Content-type", "text/plain")
        	s.end_headers()
        	s.wfile.write('Triggered!')
    		s.wfile.write('\n')	
        	return


        s.send_header("Content-type", "text/html")
        s.end_headers()

        s.wfile.write('Hello Pussy Cat!')
    	s.wfile.write('\n')	


	        




class HTTPThread (threading.Thread):
    def __init__(self, port):
        threading.Thread.__init__(self)
        self.port = port
        self.daemon = True

    def run(self):
        logging.info("Starting HTTP Thread" )
        #self.run_http(self.port)
        server_class = BaseHTTPServer.HTTPServer
        httpd = server_class(('', self.port), MyHandler)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        httpd.server_close()
        logging.info("Exiting HTTP Thread" )


	def run_http(self, port):
	    server_class = BaseHTTPServer.HTTPServer
	    httpd = server_class(('', port), MyHandler)
	    try:
	        httpd.serve_forever()
	    except KeyboardInterrupt:
	        pass
	    httpd.server_close()


def main(): 
	global IMAGES, ARGS
	parser = argparse.ArgumentParser(description='Cheshire cat capture')
	parser.add_argument('--log', help='Log file path', default='cheshire.log')
	parser.add_argument('--mail_from', help='Source mail address', required = True)
	parser.add_argument('--mail_to', help='Target mail address', default=None, nargs='*', required = True)
	parser.add_argument('--mail_smtp', help='SMTP server', required = True)
	parser.add_argument('--output', help='Path to write images to', default = '.')
	parser.add_argument('--http_port', help='Port number of HTTP server', default=9090)
	parser.add_argument('--motion', help='Motion threshold to filter', default=20000, type = int)


	ARGS = parser.parse_args()
	logFormatter = logging.Formatter("%(asctime)s [%(levelname)-5.5s]  %(message)s")
	rootLogger = logging.getLogger()
	fileHandler = logging.handlers.RotatingFileHandler(ARGS.log, maxBytes=(1024*1024*1), backupCount=5)
	fileHandler.setFormatter(logFormatter)
	rootLogger.addHandler(fileHandler)

	consoleHandler = logging.StreamHandler(sys.stdout)
	consoleHandler.setFormatter(logFormatter)
	rootLogger.addHandler(consoleHandler)

	rootLogger.setLevel(logging.INFO)
	GPIO.setmode(GPIO.BCM)

	catFlapChannel = 21
	GPIO.setup(catFlapChannel, GPIO.IN)
	# Buzzer channel
	GPIO.setup(BUZZER, GPIO.OUT)
	GPIO.setup(LED_YELLOW, GPIO.OUT)
	GPIO.setup(LED_GREEN, GPIO.OUT)
	GPIO.setup(LED_RED, GPIO.OUT)

	GPIO.add_event_detect(catFlapChannel, GPIO.FALLING, callback=onCatFlapTriggered_debouncer, bouncetime=100)
	
	pygame.mixer.init()
	pygame.mixer.music.load('cheshire.mp3')
	#onCatFlapTriggered()

	logging.info( "Cheshire Cat Flap Camera started. Monitoring...")
	fps = 3

	catcam = urllib2.urlopen('http://db:8084/')

	ledThread = threading.Thread(target=ledLoop)
	ledThread.daemon = True
	ledThread.start()

	httpThread = HTTPThread(ARGS.http_port)
	httpThread.start()

	while True:
		#Loop
		before = datetime.now()
		if not takePhoto2(catcam):
			logging.error("Failed to retrieve image from camera. Not multipart?")
			break
		duration = (before - datetime.now()).total_seconds()
		s = max(0, (1 / fps) - duration)
		logging.info("Waiting for {} ".format(s))
		time.sleep(s)

	catcam.close()








if __name__ == '__main__':
	try:
		main()
	except KeyboardInterrupt as e:
		traceback.print_exc()
	except Exception as e:
		traceback.print_exc()     
	sys.exit(0)    

