cmake_minimum_required(VERSION 3.20)
project(vkreplay CXX)

# Inputs (all pinned by the image build, see images/pins.lock):
#   VK_XML         the Vulkan registry the tables are generated from
#   JSON_INCLUDE   nlohmann/json's single_include directory
#   CMAKE_PREFIX_PATH  where the Vulkan headers and loader are installed
set(VK_XML "" CACHE FILEPATH "vk.xml from the pinned Vulkan-Headers")
set(JSON_INCLUDE "" CACHE PATH "nlohmann/json single_include directory")
if(NOT VK_XML OR NOT JSON_INCLUDE)
  message(FATAL_ERROR "set -DVK_XML=... and -DJSON_INCLUDE=...")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Vulkan REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

set(GEN ${CMAKE_CURRENT_BINARY_DIR}/gen)
add_custom_command(
  OUTPUT ${GEN}/vk_tables.inc ${GEN}/formats.json
  COMMAND ${CMAKE_COMMAND} -E make_directory ${GEN}
  COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/codegen/gen_vk_tables.py ${VK_XML}
          ${GEN}/vk_tables.inc ${GEN}/formats.json
  DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/codegen/gen_vk_tables.py ${VK_XML}
  COMMENT "Generating Vulkan tables from vk.xml")

add_executable(vkreplay
  src/main.cpp src/parent.cpp src/protocol.cpp src/case_model.cpp src/vk_tables.cpp
  src/child.cpp src/child_setup.cpp src/child_resources.cpp src/child_pipelines.cpp
  src/child_commands.cpp src/child_sync_output.cpp
  ${GEN}/vk_tables.inc)
target_include_directories(vkreplay PRIVATE src ${GEN} ${JSON_INCLUDE})
target_link_libraries(vkreplay PRIVATE Vulkan::Vulkan)
target_compile_options(vkreplay PRIVATE -Wall -Wno-missing-field-initializers)
install(TARGETS vkreplay DESTINATION bin)
install(FILES ${GEN}/formats.json DESTINATION share/vkreplay)
