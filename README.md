# AIM Glockenspiel Robot — Hardware & Control

An autonomous mechatronic instrument performance platform developed as part of the **AI4Musicians (AIM)** team under Purdue University's **Vertically Integrated Projects (VIP)** program.

This repository houses the embedded firmware, solenoid driver logic, and control interfaces for driving a robotic glockenspiel.


# Project Overview
The goal of this project is to develop a mechatronic glockenspiel performance setup capable of controlled timing, strike velocity, and millisecond-accurate musical execution.

Microcontroller: Raspberry Pi Pico 2 / RP2350

Actuation Strategy: Linear Push/Pull Solenoids with Transistor/MOSFET Driver Circuits

Primary Focus: Pulse duration timing (preventing double-strikes/buzzing), velocity control, and host trigger integration.

# Tech Stack & Hardware Components
Language: MicroPython / C++ (Pico SDK)

Microcontroller: Raspberry Pi RP2350

Peripherals: GPIO (Digital Output, PWM pulse control), Flyback Diodes, Transistor/MOSFET Driver Array

Host Communication: USB / Serial (MIDI Event Triggers
