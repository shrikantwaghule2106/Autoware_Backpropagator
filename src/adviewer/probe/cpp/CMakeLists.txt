cmake_minimum_required(VERSION 3.16)
project(adviewer_probe CXX)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()
add_compile_options(-Wall -Wextra -Wpedantic)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)

add_executable(probe src/probe.cpp)
target_link_libraries(probe rclcpp::rclcpp)

install(TARGETS probe DESTINATION lib/${PROJECT_NAME})
ament_package()
