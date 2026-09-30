cmake_minimum_required(VERSION 3.20)
project(vk_candidate C CXX)

# The graded build is exactly:
#   cmake -S /task -B /task/build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build /task/build
# and must produce /task/build/libvk_candidate.so: one self-contained shared
# library (link everything else statically). Everything below is yours to change.

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Vulkan headers only: an ICD must not link the Vulkan loader.
find_path(VULKAN_INCLUDE_DIR vulkan/vulkan_core.h PATHS /opt/vk/include /usr/include REQUIRED)

add_library(vk_candidate SHARED src/icd.cpp)
target_include_directories(vk_candidate PRIVATE ${VULKAN_INCLUDE_DIR})
set_target_properties(vk_candidate PROPERTIES LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})
target_link_options(vk_candidate PRIVATE -static-libstdc++ -static-libgcc -Wl,--no-undefined)
