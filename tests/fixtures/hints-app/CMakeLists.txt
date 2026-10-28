# Like ZMK's app/CMakeLists.txt: Zephyr is found relative to a west layout, not through
# $ENV{ZEPHYR_BASE} (regression fixture for docs/DOGFOOD-1.md).
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS ../zephyr)
project(hints_app)
target_sources(app PRIVATE src/main.c)
