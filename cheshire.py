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
from email.mime.image import MIMEImage
from email.utils import COMMASPACE, formatdate
import smtplib
from PIL import Image
import tempfile
import logging
import logging.handlers
from collections import deque
import os
import itertools
import argparse
import threading
import pygame
import exifread
from flask import Flask
import flask
import StringIO
import shutil



app = Flask(__name__)
app.secret_key = 'extremely advanced catflap monitor'


IMAGES = deque()
EVENTS = deque()
ARGS = None
DEBOUNCE_TIMER = None
BUZZER = 18
LED_RED = 27
LED_GREEN = 22
LED_YELLOW = 17
SWITCH_BIG = 25
SWITCH_SMALL = 24
CATFLAP_TRIGGER = 21
MUSIC_MAIN = 'cheshire.mp3'
MUSIC_FUN = 'cheshire_fun.mp3'



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
		self.img_idx = 0
		self.id = datetime.now().strftime('%Y%m%d_%H%M%S')
		store_dir = os.path.join(ARGS.persist_image_store_dir, self.id)
		if not os.path.exists(store_dir):
			os.makedirs(store_dir)
		for i in images:
			# Copy original image to the event store
			shutil.copy(i.filename, store_dir)

	def getNextImage(self):
		self.img_idx += 1
		if self.img_idx >= len(self.images): self.img_idx = 0 
		logging.debug("Event contains {} images, and index is {}".format(len(self.images), self.img_idx))
		return self.images[self.img_idx]


	def unlink(self):
		try:
			for i in self.images:
				os.unlink(i.filename)
		except:
			pass



def send_mail(send_from, send_to, subject, text, files=None,
              server="127.0.0.1", webserver_urlbase=None):
    assert isinstance(send_to, list)

    msg = MIMEMultipart('alternative')
    msg['From'] = send_from
    msg['To'] = COMMASPACE.join(send_to)
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = subject

    plain_part = MIMEText(text, 'plain')
    msg.attach(plain_part)

    html_part = """
    	<html>
    		<head></head>
	    	<body>
	    		<h2>Rosie Alert!!</h2>
	    		{images}
	    	</body>
	   	</html> 
    """


    image_html = ''
    idx = 0
    for f in files or []:

        #  add as a thumbnail
    	with tempfile.TemporaryFile() as thumb_file:
    		img = Image.open(f)
    		img.thumbnail( (350,350), Image.ANTIALIAS)
    		img.save(thumb_file, 'JPEG', quality=20, optimize=True)
    		thumb_file.seek(0)
    		msg_image = MIMEImage(thumb_file.read())
        	msg_image.add_header('Content-ID','<image{}>'.format(idx))
        	msg.attach(msg_image)

        image_html = '{orig}<a href="{urlbase}/{fname}"><img src="cid:image{idx}"/></a><br/>'.format(
        					 orig=image_html, idx=idx, urlbase=webserver_urlbase, fname=basename(f))
        idx += 1

    html_part = html_part.format(images=image_html)
    msg.attach(MIMEText(html_part, 'html'))
    smtp = smtplib.SMTP(server, 587)
    smtp.set_debuglevel(True)
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
			# Images belonging to events are still referenced so do not delete here.
			# they will be deleted elsewhere by the saveEvent processing. 
			# isEvent would better be replaced with a reference counter idea.
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
				if motion >= threshold :
					f_imgs.append(i)
	return f_imgs




def make_event(images):
	ev = Event(images)
	global EVENTS
	EVENTS.appendleft(ev)
	# Delete old events. This would be better if called explicitly and isEvent
	# changed to a reference counter.
	if len(EVENTS) >= 5:
		r = EVENTS.pop()
		r.unlink()
	return ev




def onCatFlapTriggered(motion_threshold):
	global IMAGES

	logging.info("Cat flap actually triggered... {}".format(motion_threshold))

	imgs = applyMotionFilter(list(itertools.islice(IMAGES, 0, 10)), motion_threshold)
	# Reverse so oldest image is first
	imgs = list(reversed(imgs))

	if len(imgs) == 0:
		logging.info("No images to send after motion filter applied. False alarm folks!")
		return

	# Make an event to retain the images long enough for other purposes, e.g.
	# display on the monitor, send as email etc.
	event = make_event(imgs)

	logging.info("Sending photos: {}".format(imgs))
	timestamp = datetime.now().strftime('%H:%M:%S')
	subject = "{} {}".format("ROSIE Alert", timestamp)


	send_mail(ARGS.mail_from, ARGS.mail_to, 
				subject, 'Cat Spotted!',
				[i.filename for i in imgs], ARGS.mail_smtp,
			   '{}/{}'.format(ARGS.webserver_urlbase, event.id)
			  )



def onCatFlapTriggered_debouncer(channel):
	logging.info("Cat flap triggered!")
	global DEBOUNCE_TIMER
	if DEBOUNCE_TIMER:
		logging.info("Bounce debounced.")
		DEBOUNCE_TIMER.cancel()
	
	DEBOUNCE_TIMER = threading.Timer(5, onCatFlapTriggered, [ARGS.motion])
	DEBOUNCE_TIMER.start()
	pygame.mixer.music.load(MUSIC_MAIN)
	pygame.mixer.music.play()
	for i in range(5):
		GPIO.output(BUZZER, GPIO.HIGH)
		time.sleep(0.1)
		GPIO.output(BUZZER, GPIO.LOW)
		time.sleep(0.1)


def onSmallSwitchPressed(channel):
	logging.info("Small switch pressed")
	onCatFlapTriggered_debouncer(channel)
	
def onBigSwitchPressed(channel):
	logging.info("Big switch pressed")
	pygame.mixer.music.load(MUSIC_FUN)
	pygame.mixer.music.set_volume(1.0)
	pygame.mixer.music.play()


def ledLoop():

	# led = LedDefault()

	# while True:
	# 	led.advance()
	# 	time.sleep(led.delay)
	# 	if bigButtonPressed:
	# 		led = LedFlashing()
	# 	else:
	# 		led = LedDefault()

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


def catPhotoTakerLoop():
	fps = 3
	catcam = None
	while True:
		try:
			if not catcam:
				catcam = urllib2.urlopen('http://db:8084/')
			#Loop
			before = datetime.now()
			if not takePhoto2(catcam):
				logging.error("Failed to retrieve image from camera. Not multipart?")
				catcam = None
			duration = (before - datetime.now()).total_seconds()
			s = max(0, (1 / fps) - duration)
			logging.info("Waiting for {} ".format(s))
			time.sleep(s)
		except:
			logging.exception("Problem during cat image retrieval!!!")
			catcam = None
			time.sleep(10)




################FLASK WEB SERVER#########
@app.route('/')
def flask_root():
	"""Flask Root"""
	return flask.render_template('main.html', webcam_url = '/imgcycle')


@app.route('/catflap/')
def flask_log():
	# list the top items form the persistence store back to the client in
	# descending order
	events = sorted(os.listdir(ARGS.persist_image_store_dir), reverse=True)
	events = [e for e in events if os.path.isdir(os.path.join(ARGS.persist_image_store_dir, e))]
	return flask.render_template('event_log.html', events = events)

@app.route('/catflap/event/<event_id>')
def flask_event(event_id):
	images = sorted(os.listdir(os.path.join(ARGS.persist_image_store_dir, event_id)))
	return flask.render_template('event_images.html', images = images, event_id = event_id)

@app.route('/catflap/event/<event_id>/<image>')
def flask_eventimg(event_id, image):
	return flask.send_file(os.path.join(ARGS.persist_image_store_dir, event_id, image), mimetype='image/jpeg')



@app.route('/imgcycle/')
def flask_imgcycle():
	"""Return an image from the event list, rotating on each call"""
	global EVENTS
	if not EVENTS:
		return flask.redirect('http://lorempixel.com/800/480/cats/')
	img = EVENTS[0].getNextImage()
	logging.debug("Flask Image Server. Sending browser file {}".format(img.filename))
	return flask.send_file(img.filename, mimetype='image/jpeg')

@app.route('/trigger/')
def trigger():
	onCatFlapTriggered(0)
	return 'Triggered with 0'




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
	# Support a separate webserver to save wear on SDCard (perhaps i am paranoid!)
	parser.add_argument('--persist_image_store_dir', help='Directory for long term retaining event images.')
	parser.add_argument('--webserver_urlbase', help='Web address base for serving captured images distributed by email')


	ARGS = parser.parse_args()

	if ARGS.webserver_urlbase and not ARGS.persist_image_store_dir:
		print "Missing store for webserver"
		return

	logFormatter = logging.Formatter("%(asctime)s [%(levelname)-5.5s]  %(message)s")
	rootLogger = logging.getLogger()
	fileHandler = logging.handlers.RotatingFileHandler(ARGS.log, maxBytes=(1024*1024*1), backupCount=5)
	fileHandler.setFormatter(logFormatter)
	rootLogger.addHandler(fileHandler)

	consoleHandler = logging.StreamHandler(sys.stdout)
	consoleHandler.setFormatter(logFormatter)
	rootLogger.addHandler(consoleHandler)

	app.logger.addHandler(fileHandler)


	rootLogger.setLevel(logging.INFO)
	GPIO.setmode(GPIO.BCM)

	GPIO.setup(CATFLAP_TRIGGER, GPIO.IN)
	GPIO.setup(SWITCH_BIG, GPIO.IN, pull_up_down=GPIO.PUD_UP)
	GPIO.setup(SWITCH_SMALL, GPIO.IN, pull_up_down=GPIO.PUD_UP)

	# Buzzer channel
	GPIO.setup(BUZZER, GPIO.OUT)
	GPIO.setup(LED_YELLOW, GPIO.OUT)
	GPIO.setup(LED_GREEN, GPIO.OUT)
	GPIO.setup(LED_RED, GPIO.OUT)

	GPIO.add_event_detect(CATFLAP_TRIGGER, GPIO.FALLING, callback=onCatFlapTriggered_debouncer, bouncetime=300)
	GPIO.add_event_detect(SWITCH_BIG, GPIO.FALLING, callback=onBigSwitchPressed, bouncetime=200)
	GPIO.add_event_detect(SWITCH_SMALL, GPIO.FALLING, callback=onSmallSwitchPressed, bouncetime=200)


	pygame.mixer.init()
	pygame.mixer.music.set_volume(1.0)
	pygame.mixer.music.load(MUSIC_MAIN)

	logging.info( "Cheshire Cat Flap Camera started. Monitoring...")


	ledThread = threading.Thread(target=ledLoop)
	ledThread.daemon = True
	ledThread.start()

	catCamThread = threading.Thread(target=catPhotoTakerLoop)
	catCamThread.daemon = True
	catCamThread.start()

	# Start Flask
	app.run(host='0.0.0.0', port=9090, debug=False)








if __name__ == '__main__':
	try:
		main()
	except KeyboardInterrupt as e:
		traceback.print_exc()
	except Exception as e:
		traceback.print_exc()     
	sys.exit(0)    

