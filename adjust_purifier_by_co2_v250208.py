import paho.mqtt.client as mqtt
import json
from datetime import datetime
from miio import Device, DeviceFactory, DeviceStatus
from paho.mqtt.enums import CallbackAPIVersion
import time
import threading
import re
import requests


# 配置类修改
class Config:
    def __init__(self):
        with open('config.json', 'r', encoding='utf-8') as f:
            json_str = f.read()
            json_str = re.sub(r'(?<!http:)(?<!https:)//.*?\n', '\n', json_str)       # 删除注释
            config = json.loads(json_str)
        self.mqtt = config['mqtt']
        self.purifier = config['purifier']
        self.bark = config['bark']

# 传感器数据配置
class SensorConfig:
    DATA_ITEMS = [
        "空气质量",          # index 0
        "PM2.5质量浓度",     # index 8
        "PM10质量浓度",      # index 9
        "甲醛",             # index 10
        "二氧化碳"          # index 11
    ]
    DATA_INDICES = [0, 8, 9, 10, 11]

class MiIotDevice:
    def __init__(self, ip, token):
        self.device: Device = DeviceFactory.create(ip, token)
        self.device_info = self.get_info()

    def get_info(self):
        return {
            "ip": self.device.ip,
            "model": self.device.model,
            "device_id": "nx_"+str(self.device.device_id),
            "mi_device_id": self.device.device_id
        }

    def set_properties(self, properties):
        if self.device is None:
            raise Exception("Device not ready")
        return self.device.send("set_properties", properties)

    def get_properties(self, properties):
        if self.device is None:
            raise Exception("Device not ready")
        return self.device.send("get_properties", properties)

class AirQualityController:
    def __init__(self, device, config):
        self.device = device
        self.config = config
        self.current_state = None

    def _get_current_state(self):
        try:
            properties = [
                {'did': self.device.device_info['mi_device_id'], 'siid': 2, 'piid': 1},  # power
                {'did': self.device.device_info['mi_device_id'], 'siid': 2, 'piid': 4},  # mode
                {'did': self.device.device_info['mi_device_id'], 'siid': 2, 'piid': 5}   # speed
            ]
            result = self.device.get_properties(properties)
            return {
                'power': result[0]['value'],
                'mode': result[1]['value'],
                'speed': result[2]['value']
            }
        except Exception as e:
            print(f"获取设备状态失败: {e}")
            return None

    def send_notification(self, message):
        bark_url = self.config.bark['api_url']
        if bark_url:
            try:
                url = bark_url % message
                requests.get(url)
            except Exception as e:
                print(f"发送通知失败: {e}")
        

    def control_based_on_data(self, co2_value, pm25_value):
        try:
            self.current_state = self._get_current_state()
            if self.current_state is None:
                return

            # 找到当前空气质量值所在的区间
            ranges = self.config.purifier['air_quality_ranges']
            current_range = None
            
            # 寻找满足条件的最高等级区间
            for range_config in ranges:
                if co2_value >= range_config['co2_threshold'] or pm25_value >= range_config['pm25_threshold']:
                    current_range = range_config
                    break
            
            if current_range:
                if current_range['action'] == 'off':
                    if self.current_state['power']:
                        self._set_switch_off()
                        message = f"当前二氧化碳: {co2_value}，PM2.5: {pm25_value}，关闭设备"
                        print(message)
                        self.send_notification(message)
                else:
                    self._apply_action(current_range, co2_value, pm25_value)
            else:
                print(f"当前二氧化碳: {co2_value}，PM2.5: {pm25_value}，没有找到匹配的设置范围")

        except Exception as e:
            print(f"控制空气净化器失败: {e}")

    def _apply_action(self, range_config, co2_value, pm25_value):
        action = range_config['action']
        trigger_message = f"当前二氧化碳浓度: {co2_value}，PM2.5: {pm25_value}"
        
        if action == 'sleep' and (not self.current_state['power'] or 
                                self.current_state['mode'] != self.config.purifier['modes']['sleep']):
            self._set_sleep_mode()
            message = f"{trigger_message}，切换到睡眠模式"
            print(message)
            self.send_notification(message)
        elif action == 'favorite' and (not self.current_state['power'] or 
                                     self.current_state['mode'] != self.config.purifier['modes']['favorite']):
            self._set_favorite_mode()
            message = f"{trigger_message}，切换到最爱模式"
            print(message)
            self.send_notification(message)
        elif action == 'manual' and (not self.current_state['power'] or 
                                   self.current_state['mode'] != self.config.purifier['modes']['none'] or 
                                   self.current_state['speed'] != range_config['speed']):
            self._set_manual_mode(range_config['speed'])
            message = f"{trigger_message}，切换到手动{range_config['speed']} + 1档"
            print(message)
            self.send_notification(message)
        else:
            print(f"{trigger_message}，设备状态无需调整")

    def _set_sleep_mode(self):
        self.device.set_properties([
            {'did': self.device.device_info['mi_device_id'], 'value': True, 'siid': 2, 'piid': 1},
            {'did': self.device.device_info['mi_device_id'], 'value': self.config.purifier['modes']['sleep'], 'siid': 2, 'piid': 4}
        ])

    def _set_favorite_mode(self):
        self.device.set_properties([
            {'did': self.device.device_info['mi_device_id'], 'value': True, 'siid': 2, 'piid': 1},
            {'did': self.device.device_info['mi_device_id'], 'value': self.config.purifier['modes']['favorite'], 'siid': 2, 'piid': 4}
        ])

    def _set_switch_off(self):
        self.device.set_properties([
            {'did': self.device.device_info['mi_device_id'], 'value': False, 'siid': 2, 'piid': 1}
        ])

    def _set_manual_mode(self, speed):
        self.device.set_properties([
            {'did': self.device.device_info['mi_device_id'], 'value': True, 'siid': 2, 'piid': 1},
            {'did': self.device.device_info['mi_device_id'], 'value': self.config.purifier['modes']['none'], 'siid': 2, 'piid': 4},
            {'did': self.device.device_info['mi_device_id'], 'value': speed, 'siid': 2, 'piid': 5}
        ])

class MQTTHandler:
    def __init__(self):
        self.config = Config()
        self.device = MiIotDevice(self.config.purifier['ip'], self.config.purifier['token'])
        self.controller = AirQualityController(self.device, self.config)
        self.client = self._setup_mqtt_client()
        self.last_process_time = 0
        self.running = True  # 添加运行状态控制

    def _setup_mqtt_client(self):
        client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
        client.username_pw_set(self.config.mqtt['username'], self.config.mqtt['password'])
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        return client

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            print("已连接到MQTT服务器")
            topic = f"{self.config.mqtt['username']}/airnow/{self.config.mqtt['device_mac']}/event/post"
            client.subscribe(topic)
            # 发送初始数据请求
            self._request_data()
        else:
            print(f"连接失败，返回码: {rc}")

    def _request_data(self):
        """发送数据请求"""
        publish_topic = f"{self.config.mqtt['username']}/airnow/{self.config.mqtt['device_mac']}/event/set"
        self.client.publish(publish_topic, json.dumps({"params": "appActive"}))

    def _process_sensor_data(self, data):
        print(f"\n时间: {datetime.fromtimestamp(data['t'] - 8 * 3600).strftime('%Y-%m-%d %H:%M:%S')}")  # 调整为UTC+8时区
        co2_value = 0
        pm25_value = 0
        
        for i, value in enumerate(data["body"]):
            if i == 10:  # 甲醛
                value = f"{value/1000:.3f}"
            elif i in [12, 13]:  # 温度和湿度
                value = f"{value/10:.1f}"
            
            if i in SensorConfig.DATA_INDICES:
                index = SensorConfig.DATA_INDICES.index(i)
                print(f"{SensorConfig.DATA_ITEMS[index]}: {value}")
            
            if i == 11:  # 二氧化碳
                co2_value = int(value)
            elif i == 8:  # PM2.5
                pm25_value = int(value)
        
        # 使用两个指标控制设备
        self.controller.control_based_on_data(co2_value, pm25_value)

    def _on_message(self, client, userdata, msg):
        try:
            current_time = time.time()
            if current_time - self.last_process_time < 60:  # 控制数据处理间隔为60秒
                return

            data = json.loads(msg.payload.decode())
            if "body" in data:
                self._process_sensor_data(data)
                self.last_process_time = current_time
                # 设置定时器，60秒后请求新数据
                threading.Timer(60, self._request_data).start()
        except Exception as e:
            print(f"数据解析错误: {e}")

    def stop(self):
        """停止MQTT客户端"""
        self.running = False
        try:
            self.client.disconnect()
            self.client.loop_stop()
        except Exception as e:
            print(f"断开连接时发生错误: {e}")

    def start(self):
        try:
            self.client.connect(self.config.mqtt['broker'], self.config.mqtt['port'], 60)
            print("正在连接到MQTT服务器...")
            
            # 在单独的线程中运行MQTT循环
            self.client.loop_start()
            
            # 主循环
            while self.running:
                try:
                    time.sleep(1)
                except KeyboardInterrupt:
                    print("\n正在优雅地关闭程序...")
                    self.stop()
                    break
                
        except Exception as e:
            print(f"连接错误: {e}")

def main():
    mqtt_handler = MQTTHandler()
    try:
        mqtt_handler.start()
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    finally:
        mqtt_handler.stop()


if __name__ == "__main__":
    main()
