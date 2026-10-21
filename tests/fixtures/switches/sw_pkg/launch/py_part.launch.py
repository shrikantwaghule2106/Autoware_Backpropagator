from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("use_monitor", default_value="true"),
        Node(package="sw_pkg", executable="m", name="monitor", condition=IfCondition(LaunchConfiguration("use_monitor"))),
    ])
