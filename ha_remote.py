#!/usr/bin/python
# -*- coding:utf-8 -*-

# Make sure to enable SPI and I2C with raspi-config and reboot
# sudo apt update && sudo apt install python3-rpi.gpio python3-spidev python3-requests python3-numpy python3-pil python3-smbus

import os
import time
import logging
import threading
import requests
from enum import Enum
from PIL import Image, ImageDraw, ImageFont
from TP_lib import gt1151, epd2in13_V4

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Constants and Configuration
REFRESH_RATE = 1.0 / 30  # 30Hz refresh rate
PARTIAL_REFRESH_INTERVAL = 180  # 3 minutes in seconds
FULL_REFRESH_INTERVAL = 900    # 15 minutes in seconds

# File paths
picdir = os.path.join(os.path.dirname(__file__), 'imgs')
fontdir = os.path.join(os.path.dirname(__file__), 'fonts')

# Home Assistant configuration
HASS_URL = "http://homeassistant.local:8123"
with open(os.path.join(os.path.dirname(__file__), 'token'), 'r') as f:
    HASS_TOKEN = f.read().strip()
HEADERS = {
    "Authorization": f"Bearer {HASS_TOKEN}",
    "Content-Type": "application/json",
}

class Page(Enum):
    MOVIE_ON = 0
    MOVIE_OFF = 1
    AIRPLAY = 2
    BUTTON_PAGE = 3
    POWER_STATS = 4
    SHUTDOWN = 5

    @classmethod
    def next_page(cls, current):
        return cls((current.value + 1) % len(cls))
    
    @classmethod
    def prev_page(cls, current):
        return cls(current.value - 1 if current.value > 0 else len(cls) - 1)

class HomeAssistantAPI:
    @staticmethod
    def call_service(service_name):
        try:
            response = requests.post(
                f"{HASS_URL}/api/services/script/turn_on",
                headers=HEADERS,
                json={"entity_id": f"script.{service_name}"}
            )
            return response.status_code == 200
        except Exception as e:
            logging.error(f"Failed to call service {service_name}: {e}")
            return False

    @staticmethod
    def get_state(entity_id):
        try:
            response = requests.get(
                f"{HASS_URL}/api/states/{entity_id}",
                headers=HEADERS
            )
            return response.json()['state']
        except Exception as e:
            logging.error(f"Failed to get state for {entity_id}: {e}")
            return "Error"

    @staticmethod
    def get_media_plug_state():
        try:
            state = HomeAssistantAPI.get_state("switch.media_rpi_plug")
            return state == 'on'
        except Exception as e:
            logging.error(f"Failed to get media plug state: {e}")
            return False

    @staticmethod
    def get_power_stats():
        try:
            pv_power = HomeAssistantAPI.get_state("sensor.my_reasonable_pv_production")
            battery = HomeAssistantAPI.get_state("sensor.battery_power_available")
            consumption = HomeAssistantAPI.get_state("sensor.my_power_consumption")
            return pv_power, battery, consumption
        except Exception as e:
            logging.error(f"Failed to get power stats: {e}")
            return "Error", "Error", "Error"

class Display:
    def __init__(self):
        self.epd = epd2in13_V4.EPD()
        self.image = Image.new('1', (self.epd.height, self.epd.width), 255)
        self.draw = ImageDraw.Draw(self.image)
        self.font24 = ImageFont.truetype(os.path.join(fontdir, 'Font.ttc'), 24)
        
    def init(self):
        self.epd.init(self.epd.FULL_UPDATE)
        self.epd.Clear(0xFF)
        
    def draw_page(self, page):
        # Clear the image
        self.draw.rectangle([(0, 0), (self.epd.height, self.epd.width)], fill=255)
        
        # Draw navigation arrows
        self.draw.text((10, 20), "←", font=self.font24, fill=0)
        self.draw.text((210, 20), "→", font=self.font24, fill=0)
        
        # Get media plug state for relevant pages
        media_is_on = HomeAssistantAPI.get_media_plug_state() if page in [Page.MOVIE_ON, Page.AIRPLAY] else False
        
        if page == Page.MOVIE_ON:
            self.draw.rectangle([(60, 40), (180, 100)], outline=0, fill=0 if media_is_on else None)
            self.draw.text((65, 55), "Movie On", font=self.font24, fill=255 if media_is_on else 0)
        elif page == Page.MOVIE_OFF:
            self.draw.rectangle([(60, 40), (180, 100)], outline=0)
            self.draw.text((65, 55), "Media Off", font=self.font24, fill=0)
        elif page == Page.AIRPLAY:
            self.draw.rectangle([(60, 40), (180, 100)], outline=0, fill=0 if media_is_on else None)
            self.draw.text((70, 55), "Airplay On", font=self.font24, fill=255 if media_is_on else 0)
        elif page == Page.BUTTON_PAGE:
            # Draw three buttons side by side
            button_width = 60
            spacing = 10
            start_x = 25
            
            # Button D
            self.draw.rectangle([(start_x, 50), (start_x + button_width, 90)], outline=0)
            self.draw.text((start_x + 25, 60), "D", font=self.font24, fill=0)
            
            # Button K
            self.draw.rectangle([(start_x + button_width + spacing, 50), 
                               (start_x + 2*button_width + spacing, 90)], outline=0)
            self.draw.text((start_x + button_width + spacing + 25, 60), "K", font=self.font24, fill=0)
            
            # Button E
            self.draw.rectangle([(start_x + 2*button_width + 2*spacing, 50), 
                               (start_x + 3*button_width + 2*spacing, 90)], outline=0)
            self.draw.text((start_x + 2*button_width + 2*spacing + 25, 60), "E", font=self.font24, fill=0)
        elif page == Page.POWER_STATS:
            pv_power, battery, consumption = HomeAssistantAPI.get_power_stats()
            self.draw.text((60, 30), f"PV: {pv_power}W", font=self.font24, fill=0)
            self.draw.text((60, 60), f"Battery: {battery}kWh", font=self.font24, fill=0)
            self.draw.text((60, 90), f"Use: {consumption}W", font=self.font24, fill=0)
        elif page == Page.SHUTDOWN:
            self.draw.rectangle([(60, 40), (180, 100)], outline=0)
            self.draw.text((65, 55), "Shutdown", font=self.font24, fill=0)

    def refresh(self, partial=True):
        if partial:
            self.epd.displayPartial_Wait(self.epd.getbuffer(self.image))
        else:
            self.epd.init(self.epd.FULL_UPDATE)
            self.epd.displayPartBaseImage(self.epd.getbuffer(self.image))
            self.epd.init(self.epd.PART_UPDATE)

    def shutdown(self):
        self.epd.sleep()
        time.sleep(1)
        self.epd.Dev_exit()

class TouchInput:
    def __init__(self):
        self.gt = gt1151.GT1151()
        self.GT_Dev = gt1151.GT_Development()
        self.GT_Old = gt1151.GT_Development()
        self.flag_t = 1
        
    def init(self):
        self.gt.GT_Init()
        self.thread = threading.Thread(target=self._irq_thread)
        self.thread.start()

    def _irq_thread(self):
        print("pthread running")
        while self.flag_t == 1:
            time.sleep(0.01) # 10ms
            if self.gt.digital_read(self.gt.INT) == 0:
                self.GT_Dev.Touch = 1
            else:
                self.GT_Dev.Touch = 0
        print("thread:exit")
        
    def read(self):
        self.gt.GT_Scan(self.GT_Dev, self.GT_Old)
        if self.GT_Old.X[0] == self.GT_Dev.X[0] and self.GT_Old.Y[0] == self.GT_Dev.Y[0] and self.GT_Old.S[0] == self.GT_Dev.S[0]:
            return None
        if not self.GT_Dev.TouchpointFlag:
            return None
        self.GT_Dev.TouchpointFlag = 0
        return self.GT_Dev.Y[0]
        
    def cleanup(self):
        self.flag_t = 0
        self.thread.join()

def main():
    try:
        display = Display()
        touch = TouchInput()
        
        logging.info("Initializing display and touch...")
        display.init()
        touch.init()
        
        current_page = Page.MOVIE_ON
        display.draw_page(current_page)
        display.refresh(partial=False)
        
        last_partial_refresh = time.time()
        last_full_refresh = time.time()
        
        def delayed_refresh():
            time.sleep(2)
            display.draw_page(current_page)
            display.refresh()
            
        def handle_shutdown():
            touch.cleanup()
            display.shutdown()
            os.system("shutdown -h now")
            
        while True:
            time.sleep(REFRESH_RATE)
            current_time = time.time()
            
            # Handle periodic refreshes
            if current_time - last_full_refresh >= FULL_REFRESH_INTERVAL:
                display.refresh(partial=False)
                last_full_refresh = current_time
                last_partial_refresh = current_time
            elif current_time - last_partial_refresh >= PARTIAL_REFRESH_INTERVAL:
                if current_page == Page.POWER_STATS:
                    display.draw_page(current_page)
                display.refresh()
                last_partial_refresh = current_time
                
            # Handle touch input
            touch_pos = touch.read()
            if touch_pos is not None:
                if touch_pos < 40:  # Top (next)
                    current_page = Page.next_page(current_page)
                    display.draw_page(current_page)
                    display.refresh()
                elif touch_pos > 200:  # Bottom (prev)
                    current_page = Page.prev_page(current_page)
                    display.draw_page(current_page)
                    display.refresh()
                elif 50 < touch_pos < 190:  # Middle (action)
                    if current_page == Page.MOVIE_ON:
                        if HomeAssistantAPI.call_service("turn_on_movie_system"):
                            threading.Thread(target=delayed_refresh).start()
                    elif current_page == Page.MOVIE_OFF:
                        if HomeAssistantAPI.call_service("turn_off_movie_system"):
                            threading.Thread(target=delayed_refresh).start()
                    elif current_page == Page.AIRPLAY:
                        if HomeAssistantAPI.call_service("turn_on_airplay_2"):
                            threading.Thread(target=delayed_refresh).start()
                    elif current_page == Page.BUTTON_PAGE:
                        # Handle the three buttons based on X position
                        x_pos = touch.GT_Dev.X[0]
                        button_width = 60
                        spacing = 10
                        start_x = 25
                        
                        # Button D (left)
                        if start_x <= x_pos < start_x + button_width:
                            if HomeAssistantAPI.call_service("clean_dining_area"):
                                threading.Thread(target=delayed_refresh).start()
                        # Button K (middle)
                        elif start_x + button_width + spacing <= x_pos < start_x + 2*button_width + spacing:
                            if HomeAssistantAPI.call_service("clean_living_room_kitchen"):
                                threading.Thread(target=delayed_refresh).start()
                        # Button E (right)
                        elif start_x + 2*button_width + 2*spacing <= x_pos < start_x + 3*button_width + 2*spacing:
                            if HomeAssistantAPI.call_service("clean_entrance"):
                                threading.Thread(target=delayed_refresh).start()
                    elif current_page == Page.SHUTDOWN:
                        handle_shutdown()
                    display.draw_page(current_page)
                    display.refresh()
                    
    except KeyboardInterrupt:
        logging.info("Ctrl+C detected, cleaning up...")
        touch.cleanup()
        display.shutdown()
    except BaseException as e:
        logging.error(f"Unexpected error: {e}")
        touch.cleanup()
        display.shutdown()

if __name__ == "__main__":
    main()