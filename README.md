# Seplos BMS v3 measurements RS485 sniffer

Seplos BMS v3.0 broadcasts all measurements on its serial RS485 ports. Voltages, current, SOC, temperatures and more. This sniffer exposes all readings in realtime via JSON file(s) ready for further processing.

## Hardware required
- BMS (Battery Management System) [Seplos version 3.0](https://www.seplos.com/seplos-bms-3.0.html) (2 or more units)
- RS485 to USB adapter (1 unit)

Tested with BMS firmware version `1.5`. 

## Parallel mode only
Unique feature of Seplos BMS v3.0 is that broadcasts only occur when there's 2 or more units running in parallel. **This program can't work with single battery pack.**

### Single RS485 link reads all
One instance of this program and one RS485 to USB adapter is enough. Just connect to any single of the parallelled battery packs and get readings from all.

## One battery pack = one file
Example for 3 battery packs (ie. 3 BMS units) running in parallel:
```sh
linux:~$ ls -la /dev/shm/seplos_bms*json
-rw-r--r-- 677 Mar 18 11:09 /dev/shm/seplos_bms_unit1.json
-rw-r--r-- 674 Mar 18 11:09 /dev/shm/seplos_bms_unit2.json
-rw-r--r-- 679 Mar 18 11:09 /dev/shm/seplos_bms_unit3.json
```

## Installation

Download sources
```sh
cd /opt
git clone https://github.com/vpithart/seplos-bms-reader.git
cd seplos-bms-reader
```

Configure
```sh
cp seplosbms3reader.ini.default seplosbms3reader.ini
vim seplosbms3reader.ini
# set your serial port (/dev/ttyUSB0 or similar) and save
```

Test
```sh
./seplosbms3reader.py
```

Observe
```
Opening serial interface, port: /dev/ttyUSB0 19200 8N1
...
Seplos BMS: 3 units detected.
Unit 1: 56.5% 53.23V Charge 18.29A
Unit 2: 58.6% 53.21V Charge 18.03A
Unit 3: 57.7% 53.25V Charge 18.42A
```

Run automatically via systemd (debian, ubuntu)
```sh
sudo su -
cd /opt/seplos-bms-reader

ln -s /opt/seplos-bms-reader/seplosbms3reader.service /usr/lib/systemd/system/seplosbms3reader.service

systemctl daemon-reload
systemctl start seplosbms3reader
systemctl enable seplosbms3reader
```

Read the logs
```sh
journalctl -f -u seplosbms3reader
```

## Readings file format
Example file contents for battery pack 1 of 3:
```sh
linux:~$ cat /dev/shm/seplos_bms_unit1.json | jq
{
  "pack_voltage": 53.46,
  "current": 50.63,
  "remaining_capacity": 148.04,
  "total_capacity": 304.0,
  "total_discharge_capacity": 144560,
  "soc": 48.7,
  "soh": 94.0,
  "cycles": 602,
  "average_cell_voltage": 3.341,
  "average_cell_temp": 27.0,
  "max_cell_voltage": 3.344,
  "min_cell_voltage": 3.338,
  "max_cell_temp": 27.7,
  "min_cell_temp": 26.4,
  "maxdiscurt": 150,
  "maxchgcurt": 150,
  "power": -2706,
  "cell_delta": 6,
  "cell_voltage": [
    3.339,
    3.343,
    3.34,
    3.339,
    3.342,
    3.343,
    3.341,
    3.344,
    3.343,
    3.343,
    3.34,
    3.341,
    3.339,
    3.338,
    3.344,
    3.338
  ],
  "status": {
    "tb09_string": "Charge",
    "tb09": 2,
    "tb02": 0,
    "tb03": 0,
    "tb04": 0,
    "tb05": 0,
    "tb16": 0,
    "tb06": 0,
    "tb07": 67,
    "tb08": 0,
    "tb15": 0
  }
}
```

## Credits
Based on [Seplos3MQTT](https://github.com/ferelarg/Seplos3MQTT)