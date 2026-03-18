#!/usr/bin/env python3

"""
Seplos BMSv3 RS485 reader
---------------------------------------------------------------------------


"""
# --------------------------------------------------------------------------- #
# import the various needed libraries
# --------------------------------------------------------------------------- #
import signal
import sys
import logging
import serial
import configparser
import os
from pathlib import Path
import json
from datetime import datetime

# --------------------------------------------------------------------------- #
# configure the logging system
# --------------------------------------------------------------------------- #
class myFormatter(logging.Formatter):
    def format(self, record):
        if record.levelno == logging.INFO:
            self._style._fmt = "%(message)s"
        elif record.levelno == logging.DEBUG:
            self._style._fmt = f"\033[36m%(levelname)-8s\033[0m: %(message)s"
        else:
            color = {
                logging.WARNING: 33,
                logging.ERROR: 31,
                logging.FATAL: 31,
            }.get(record.levelno, 0)
            self._style._fmt = f"\033[{color}m%(levelname)-8s %(threadName)-15s-%(module)-15s:%(lineno)-8s\033[0m: %(message)s"
        return super().format(record)

log = logging.getLogger()
handler = logging.StreamHandler()
handler.setFormatter(myFormatter())
log.setLevel(logging.DEBUG)
log.addHandler(handler)

# --------------------------------------------------------------------------- #
# declare the sniffer
# --------------------------------------------------------------------------- #
class SerialSnooper:
    
    packData = [{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}]
    anythingNew = False

    lastPrintTime = datetime.now().timestamp()

    def printStatusMinutely(self):
      
      EVERY_N_SECONDS = 60

      d = datetime.now().timestamp() - self.lastPrintTime
      if (d >= EVERY_N_SECONDS):
        self.lastPrintTime = datetime.now().timestamp()

        seenPacks = list(filter(lambda x: 'status' in x and 'soc' in x and 'pack_voltage' in x, self.packData))
        numPacks = len(seenPacks)
        if (numPacks == 0):
          log.info(f"No Seplos BMS units detected.")
        else:
          log.info(f"Seplos BMS: {numPacks} units detected.")
          for i in range(0, numPacks):
            log.info(f"Unit {i+1}: {self.packData[i]['soc']}% {self.packData[i]['pack_voltage']}V {self.tb09_status_as_string(self.packData[i]['status']['tb09'])} {self.packData[i]['current']}A")

    def __init__(self, port):
        self.port = port
        self.data = bytearray(0)
        self.trashdata = False
        self.trashdataf = bytearray(0)
        
        # init the signal handler for a clean exit
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGQUIT, self.signal_handler)

        self.countersX = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
        self.counters36 = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
        self.counters52 = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]

        log.info(f"Opening serial interface, port: {port} 19200 8N1")
        self.connection = serial.Serial(port=port, baudrate=19200, bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=None)
        log.debug(self.connection)
       
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def open(self):
        self.connection.open()

    def close(self):
        self.connection.close()
    
    def read_raw(self, n=1):
        # print(f"in waiting: {self.connection.in_waiting}")
        return self.connection.read(500)
    
    # --------------------------------------------------------------------------- #
    # configure a clean exit (even with the use of kill, 
    # may be useful if saving the data to a file)
    # --------------------------------------------------------------------------- #
    def signal_handler(self, sig, frame):
        for i in range(0, len(self.packData)):
            if (os.path.exists(f"/dev/shm/seplos_bms_unit{i+1}.json")):
                os.unlink(f"/dev/shm/seplos_bms_unit{i+1}.json")
        sys.exit(0)
    
    def to_lower_under(self, text):
        text = text.lower()
        text = text.replace(' ', '_')
        return text

    # --------------------------------------------------------------------------- #
    # Bufferise the data and call the decoder if the interframe timeout occur.
    # --------------------------------------------------------------------------- #
    def process_data(self, data):
        for dat in data:
            self.data.append(dat)
            # log.info(f"process_data influx={len(data)} self.data={len(self.data)}B")
        if len(self.data) > 20:
            # log.info(f"process_data influx={len(data)} self.data={len(self.data)}B, decode")
            self.data = self.decodeModbus(self.data)

            if (self.anythingNew):
              self.anythingNew = False
              self.dump_data_to_shm()

    def dump_data_to_shm(self):
      # print(json.dumps(self.packData))
      for i in range(0, len(self.packData)):
        
        if (not ('status' in self.packData[i] and 'soc' in self.packData[i] and 'pack_voltage' in self.packData[i])):
            continue

        filename = f"/dev/shm/seplos_bms_unit{i+1}.json"
        with open(f"{filename}.tmp", "w") as json_file:
            n = json_file.write(json.dumps(self.packData[i]))
            json_file.flush()
            os.fsync(json_file.fileno())
            os.replace(f"{filename}.tmp", filename)
    
    def tb09_status_as_string(self, tb09):
      # TB09 Status
      strStatus = ''
      if   (tb09 >> 0) & 1: strStatus = "Discharge"
      elif (tb09 >> 1) & 1: strStatus = "Charge"
      elif (tb09 >> 2) & 1: strStatus = "Floating charge"
      elif (tb09 >> 3) & 1: strStatus = "Full charge"
      elif (tb09 >> 4) & 1: strStatus = "Standby mode"
      elif (tb09 >> 5) & 1: strStatus = "Turn off"
      return strStatus

    # --------------------------------------------------------------------------- #
    # Debuffer and decode the modbus frames (Request, Response, Exception)
    # --------------------------------------------------------------------------- #
    def decodeModbus(self, data):
        modbusdata = data
        bufferIndex = 0
        
        while True:
            unitIdentifier = 0
            functionCode = 0
            readByteCount = 0
            readData = bytearray(0)
            crc16 = 0
            response = False
            needMoreData = False
            frameStartIndex = bufferIndex           
            if len(modbusdata) > (frameStartIndex + 2):
                # log.info(f"DATA: [{' '.join('%02x' % i for i in modbusdata)}]")
                
                # Unit Identifier (Slave Address)
                unitIdentifier = modbusdata[bufferIndex]
                bufferIndex += 1
                # Function Code
                functionCode = modbusdata[bufferIndex]
                bufferIndex += 1
                if functionCode == 1:
                    # Response size: UnitIdentifier (1) + FunctionCode (1) + ReadByteCount (1) + ReadData (n) + CRC (2)
                    expectedLenght = 7 # 5 + n (n >= 2)
                    if len(modbusdata) >= (frameStartIndex + expectedLenght):
                        bufferIndex = frameStartIndex + 2
                        # Read Byte Count (1)
                        readByteCount = modbusdata[bufferIndex]
                        bufferIndex += 1
                        expectedLenght = (5 + readByteCount)
                        if len(modbusdata) >= (frameStartIndex + expectedLenght):
                            # Read Data (n)
                            index = 1
                            while index <= readByteCount:
                                readData.append(modbusdata[bufferIndex])
                                bufferIndex += 1
                                index += 1
                            # CRC16 (2)
                            crc16 = (modbusdata[bufferIndex] * 0x0100) + modbusdata[bufferIndex + 1]
                            metCRC16 = self.calcCRC16(modbusdata, bufferIndex)
                            bufferIndex += 2
                            if crc16 == metCRC16:
                                if self.trashdata:
                                    self.trashdata = False
                                    self.trashdataf += "]"
                                    # log.info(self.trashdataf)
                                # log.info(f"FC01 unit{unitIdentifier} bytecount {readByteCount}")
                                response = True
                                
                                #### Pack Alarms and Status ###
                                if readByteCount == 18:   

                                    self.packData[unitIdentifier-1]['status'] = {
                                      'tb09_string': self.tb09_status_as_string(readData[8]),
                                      'tb09': readData[8],
                                      'tb02': readData[9],
                                      'tb03': readData[10],
                                      'tb04': readData[11],
                                      'tb05': readData[12],
                                      'tb16': readData[13],
                                      'tb06': readData[14],
                                      'tb07': readData[15],
                                      'tb08': readData[16],
                                      'tb15': readData[17],
                                    }
                                    self.anythingNew = True

                                    # TB07
                                    # Bit0 Discharge FET on
                                    # Bit1 Charge FET on
                                    # Bit2 Current limiting FET on
                                    # Bit3 Heating on
                                    # Bit4 Reservation
                                    # Bit5 Reservation
                                    # Bit6 Reservation
                                    # Bit7 Reservation
                                    # TB08
                                    # Bit0 low Soc alarm
                                    # Bit1 Intermittent charge
                                    # Bit2 External switch control
                                    # Bit3 Static standby and sleep mode
                                    # Bit4 History data recording
                                    # Bit5 Under Soc protect
                                    # Bit6 Acktive-Limited Current
                                    # Bit7 Passive-Limited Current
                                    # TB10
                                    # Bit0 High environment temperature alarm
                                    # Bit1 Over environment temperature protection
                                    # Bit2 Low environment temperature alarm
                                    # Bit3 Under environment temperature protection
                                    # Bit4 Power high temperature alarm
                                    # Bit5 Power over temperature protection
                                    # Bit6 Cell temperature low heating
                                    # Bit7 Cell voltage Fault
                                    # TB11
                                    # Bit0 Output short latch up
                                    # Bit1 Reservation
                                    # Bit2 Charge second level over current latch up
                                    # Bit3 Discharge second level over current latch up
                                    # Bit4 Reservation
                                    # Bit5 Reservation
                                    # Bit6 Reservation
                                    # Bit7 Reservation
                                    # TB12
                                    # Bit0 Equilibrium module to open
                                    # Bit1 Static equilibrium indicate
                                    # Bit2 Static equilibrium overtime
                                    # Bit3 Equalization temperature limit
                                    # Bit4 Reservation
                                    # Bit5 Reservation
                                    # Bit6 Reservation
                                    # Bit7 Reservation
                                    
                                modbusdata = modbusdata[bufferIndex:]
                                bufferIndex = 0
                        else:
                            needMoreData = True
                    else:
                        needMoreData = True
                # FC03 (0x03) Read Holding Registers  FC04 (0x04) Read Input Registers
                elif functionCode == 4:
                    
                    # Response size: UnitIdentifier (1) + FunctionCode (1) + ReadByteCount (1) + ReadData (n) + CRC (2)
                    expectedLenght = 7 # 5 + n (n >= 2)
                    if len(modbusdata) >= (frameStartIndex + expectedLenght):
                        bufferIndex = frameStartIndex + 2
                        # Read Byte Count (1)
                        readByteCount = modbusdata[bufferIndex]
                        bufferIndex += 1
                        expectedLenght = (5 + readByteCount)
                        if len(modbusdata) >= (frameStartIndex + expectedLenght):
                            # Read Data (n)
                            index = 1
                            while index <= readByteCount:
                                readData.append(modbusdata[bufferIndex])
                                bufferIndex += 1
                                index += 1
                            # CRC16 (2)
                            crc16 = (modbusdata[bufferIndex] * 0x0100) + modbusdata[bufferIndex + 1]
                            metCRC16 = self.calcCRC16(modbusdata, bufferIndex)
                            bufferIndex += 2
                                                        
                            if crc16 == metCRC16:

                                if self.trashdata:
                                    self.trashdata = False
                                    self.trashdataf += "]"
                                    # log.info(self.trashdataf)
                                response = True
                                # log.info(f"FC04 unit{unitIdentifier} bytecount {readByteCount}")
                                
                                # Cell Pack information #######################################
                                celdas = {}
                                if readByteCount == 52:   
                                    self.counters52[unitIdentifier-1]+= 1

                                    celda = 0
                                    
                                    self.packData[unitIdentifier-1]['cell_voltage'] = [0] * 16
                                    for i in range(0, 32, 2):
                                        celda =  (((readData[i] << 8) | readData[i + 1]) / 1000.0)
                                        self.packData[unitIdentifier-1]['cell_voltage'][int(i/2)] = celda
                                    
                                    self.anythingNew = True

                                # Pack Main information #######################################
                                elif readByteCount == 36:   
                                    self.counters36[unitIdentifier-1]+= 1
                                    
                                    readDataNumber = []

                                    for i in range(0, 36, 2):
                                        readDataNumber.append((readData[i] << 8) | readData[i + 1])

                                    # Pack Voltage
                                    self.packData[unitIdentifier-1]['pack_voltage'] = readDataNumber[0]/100.0
                                    # Current
                                    current_decimal = readDataNumber [1] if readDataNumber [1] <= 32767 else readDataNumber [1] - 65536
                                    self.packData[unitIdentifier-1]['current'] = current_decimal/100.0
                                    # Remaining Capacity
                                    self.packData[unitIdentifier-1]['remaining_capacity'] = readDataNumber[2]/100.0
                                    # Total Capacity
                                    self.packData[unitIdentifier-1]['total_capacity'] = readDataNumber[3]/100.0
                                    # Total Discharge Capacity
                                    self.packData[unitIdentifier-1]['total_discharge_capacity'] = readDataNumber[4]*10
                                    # SOC
                                    self.packData[unitIdentifier-1]['soc'] = readDataNumber[5]/10.0
                                    # SOH
                                    self.packData[unitIdentifier-1]['soh'] = readDataNumber[6]/10.0
                                    # Cycles
                                    self.packData[unitIdentifier-1]['cycles'] = readDataNumber[7]
                                    # Average Cell Voltage
                                    self.packData[unitIdentifier-1]['average_cell_voltage'] = readDataNumber[8]/1000.0
                                    # Average Cell Temp
                                    self.packData[unitIdentifier-1]['average_cell_temp'] = round ((readDataNumber[9]/10 - 273.15) ,1)
                                    # Max Cell Voltage
                                    self.packData[unitIdentifier-1]['max_cell_voltage'] = readDataNumber[10]/1000.0
                                    # Min Cell Voltage
                                    self.packData[unitIdentifier-1]['min_cell_voltage'] = readDataNumber[11]/1000.0
                                    # Max Cell Temp
                                    self.packData[unitIdentifier-1]['max_cell_temp'] = round ((readDataNumber[12]/10 - 273.15),1)
                                    # Min Cell Temp
                                    self.packData[unitIdentifier-1]['min_cell_temp'] = round ((readDataNumber[13]/10 - 273.15),1)
                                    # Reserve readDataNumber [14]
                                    # MaxDisCurt
                                    self.packData[unitIdentifier-1]['maxdiscurt'] = readDataNumber[15]
                                    # MaxChgCurt
                                    self.packData[unitIdentifier-1]['maxchgcurt'] = readDataNumber[16]    
                                    #Calculated Power end Delta
                                    self.packData[unitIdentifier-1]['power'] = int(-(current_decimal/100.0)*(readDataNumber[0]/100.0))
                                    self.packData[unitIdentifier-1]['cell_delta'] = int((readDataNumber[10]) - (readDataNumber[11]))

                                    self.anythingNew = True
                                else:
                                    self.countersX[unitIdentifier-1]+= 1
                                        
                                modbusdata = modbusdata[bufferIndex:]
                                bufferIndex = 0
                                
                        else:
                            needMoreData = True
                    else:
                        needMoreData = True
            else:
                needMoreData = True

            if needMoreData:
                return modbusdata
            elif  (response == False):
                if self.trashdata:
                    self.trashdataf += " {:02x}".format(modbusdata[frameStartIndex])
                else:
                    self.trashdata = True
                    self.trashdataf = "Ignoring data: [{:02x}".format(modbusdata[frameStartIndex])
                bufferIndex = frameStartIndex + 1
                modbusdata = modbusdata[bufferIndex:]
                bufferIndex = 0

    # --------------------------------------------------------------------------- #
    # Calculate the modbus CRC
    # --------------------------------------------------------------------------- #
    def calcCRC16(self, data, size):
        crcHi = 0XFF
        crcLo = 0xFF
        
        crcHiTable	= [	0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0,
                        0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41,
                        0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0,
                        0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40,
                        0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1,
                        0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41,
                        0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1,
                        0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41,
                        0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0,
                        0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40,
                        0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1,
                        0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40,
                        0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0,
                        0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40,
                        0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0,
                        0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40,
                        0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0,
                        0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41,
                        0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0,
                        0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41,
                        0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0,
                        0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40,
                        0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1,
                        0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41,
                        0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0,
                        0x80, 0x41, 0x00, 0xC1, 0x81, 0x40]

        crcLoTable = [  0x00, 0xC0, 0xC1, 0x01, 0xC3, 0x03, 0x02, 0xC2, 0xC6, 0x06,
                        0x07, 0xC7, 0x05, 0xC5, 0xC4, 0x04, 0xCC, 0x0C, 0x0D, 0xCD,
                        0x0F, 0xCF, 0xCE, 0x0E, 0x0A, 0xCA, 0xCB, 0x0B, 0xC9, 0x09,
                        0x08, 0xC8, 0xD8, 0x18, 0x19, 0xD9, 0x1B, 0xDB, 0xDA, 0x1A,
                        0x1E, 0xDE, 0xDF, 0x1F, 0xDD, 0x1D, 0x1C, 0xDC, 0x14, 0xD4,
                        0xD5, 0x15, 0xD7, 0x17, 0x16, 0xD6, 0xD2, 0x12, 0x13, 0xD3,
                        0x11, 0xD1, 0xD0, 0x10, 0xF0, 0x30, 0x31, 0xF1, 0x33, 0xF3,
                        0xF2, 0x32, 0x36, 0xF6, 0xF7, 0x37, 0xF5, 0x35, 0x34, 0xF4,
                        0x3C, 0xFC, 0xFD, 0x3D, 0xFF, 0x3F, 0x3E, 0xFE, 0xFA, 0x3A,
                        0x3B, 0xFB, 0x39, 0xF9, 0xF8, 0x38, 0x28, 0xE8, 0xE9, 0x29,
                        0xEB, 0x2B, 0x2A, 0xEA, 0xEE, 0x2E, 0x2F, 0xEF, 0x2D, 0xED,
                        0xEC, 0x2C, 0xE4, 0x24, 0x25, 0xE5, 0x27, 0xE7, 0xE6, 0x26,
                        0x22, 0xE2, 0xE3, 0x23, 0xE1, 0x21, 0x20, 0xE0, 0xA0, 0x60,
                        0x61, 0xA1, 0x63, 0xA3, 0xA2, 0x62, 0x66, 0xA6, 0xA7, 0x67,
                        0xA5, 0x65, 0x64, 0xA4, 0x6C, 0xAC, 0xAD, 0x6D, 0xAF, 0x6F,
                        0x6E, 0xAE, 0xAA, 0x6A, 0x6B, 0xAB, 0x69, 0xA9, 0xA8, 0x68,
                        0x78, 0xB8, 0xB9, 0x79, 0xBB, 0x7B, 0x7A, 0xBA, 0xBE, 0x7E,
                        0x7F, 0xBF, 0x7D, 0xBD, 0xBC, 0x7C, 0xB4, 0x74, 0x75, 0xB5,
                        0x77, 0xB7, 0xB6, 0x76, 0x72, 0xB2, 0xB3, 0x73, 0xB1, 0x71,
                        0x70, 0xB0, 0x50, 0x90, 0x91, 0x51, 0x93, 0x53, 0x52, 0x92,
                        0x96, 0x56, 0x57, 0x97, 0x55, 0x95, 0x94, 0x54, 0x9C, 0x5C,
                        0x5D, 0x9D, 0x5F, 0x9F, 0x9E, 0x5E, 0x5A, 0x9A, 0x9B, 0x5B,
                        0x99, 0x59, 0x58, 0x98, 0x88, 0x48, 0x49, 0x89, 0x4B, 0x8B,
                        0x8A, 0x4A, 0x4E, 0x8E, 0x8F, 0x4F, 0x8D, 0x4D, 0x4C, 0x8C,
                        0x44, 0x84, 0x85, 0x45, 0x87, 0x47, 0x46, 0x86, 0x82, 0x42,
                        0x43, 0x83, 0x41, 0x81, 0x80, 0x40]

        index = 0
        while index < size:
            crc = crcHi ^ data[index]
            crcHi = crcLo ^ crcHiTable[crc]
            crcLo = crcLoTable[crc]
            index += 1

        metCRC16 = (crcHi * 0x0100) + crcLo
        return metCRC16

# --------------------------------------------------------------------------- #
# Print the usage help
# --------------------------------------------------------------------------- #
def printHelp():
    print("\nUsage:")
    print("  python seplos3reader.py")
    print("")
    print("Seplos3reader gets the configuration from seplos3reader.ini")
    print("Remember to create the file and include the following data:")
    print("[seplos3reader]")
    print("serial = /dev/ttyUSB0")
    print("")


# --------------------------------------------------------------------------- #
# get variable config from environment or config file
# --------------------------------------------------------------------------- #

def get_config_variable(name,default='mandatory'):
   try:
      # try to get variable from environment
      value = os.getenv(name)
      if value is not None:
         return value

      # the environment variable uis not defined, find in file .ini
      config = configparser.ConfigParser()
      inifile = Path(__file__).with_name('seplosbms3reader.ini')
      config.read(inifile)
      if not config.sections():  # Verificar si se cargaron secciones
            raise FileNotFoundError()

      return config['seplosbms3reader'][name]

   except configparser.NoSectionError as e:
      if default != 'mandatory':
         return default
      else:
         print(f'Error: Section [seplosbms3reader] not found in the file seplosbms3reader.ini for variable {name}, exception: {e}')
         printHelp()
         sys.exit()
   except configparser.NoOptionError as e:
       if default != 'mandatory':
          return default
       else:
          print(f'Error: Parameter {name} not found in environment variable or in the file seplosbms3reader.ini Details: {e}')
          printHelp()
          sys.exit()
   except FileNotFoundError as e:
        if default != 'mandatory':
           return default
        else:
           print(f'Error: seplosbms3reader.ini was not found or environment variable {name} not defined.')
           printHelp()
           sys.exit()
   except Exception as e:
        print(f'Unexpected error: {e}')
        printHelp()
        sys.exit()


# --------------------------------------------------------------------------- #
# main routine
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print(" ")

    port = get_config_variable('serial')

    with SerialSnooper(port) as sniffer:
        while True:
            data = sniffer.read_raw()
            # log.info(f"read_raw {len(data)}B")
            if (len(data) > 0):
                sniffer.process_data(data)
            
            sniffer.printStatusMinutely()

        

#Master: ID: 3, Read Input Registers: 0x04, Read address: 4096, Read Quantity: 18 //Pack Main information
#Slave:  ID: 3, Read Input Registers: 0x04, Read byte count: 36, Read data: [14 94 f4 c9 56 f4 6d 60 01 a6 03 1b 03 e5 00 1a 0c dc 0b 7d 0c de 0c d9 0b 80 0b 77 00 00 00 46 00 46 03 e8]
#Master: ID: 3, Read Input Registers: 0x04, Read address: 4352, Read Quantity: 26 //Pack Cells information
#Slave:  ID: 3, Read Input Registers: 0x04, Read byte count: 52, Read data: [0c dd 0c dd 0c dd 0c dc 0c dd 0c dd 0c dc 0c dc 0c d9 0c dc 0c dd 0c de 0c de 0c dd 0c dc 0c de 0b 7e 0b 80 0b 77 0b 80 0a ab 0a ab 0a ab 0a ab 0b 81 0b 6b]
#Master: ID: 3, Read Coils: 0x01, Read address: 4608, Read Quantity: 144 //Pack Alarms and Status
#Slave:  ID: 3, Read Coils: 0x01, Read byte count: 18, Read data: [00 00 00 00 00 00 00 00 01 00 00 00 00 00 00 03 00 00]
