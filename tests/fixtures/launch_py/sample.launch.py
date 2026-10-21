"""Test fixture shaped like Autoware's nebula_node_container.launch.py (never executed)."""
from launch import LaunchDescription
from launch.actions import OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import ComposableNodeContainer, Node
from launch_ros.descriptions import ComposableNode


def launch_setup(context):
    make = LaunchConfiguration("sensor_model").perform(context)
    nodes = [
        ComposableNode(
            package="demo_filters",
            plugin="demo::CropBoxFilter",
            name="crop_box_filter",
            remappings=[
                ("input", "pointcloud_raw"),
                ("output", "self_cropped/pointcloud"),
            ],
        ),
        ComposableNode(
            package="demo_driver",
            plugin=make + "RosWrapper",
            name=make.lower() + "_driver",
            remappings=[("output", "pointcloud_raw")],
        ),
    ]
    container = ComposableNodeContainer(
        name="lidar_container",
        package="rclcpp_components",
        executable="component_container",
        composable_node_descriptions=nodes,
    )
    return [container]


def generate_launch_description():
    relay = Node(
        package="demo_tools",
        executable="relay",
        name="relay",
        namespace="tools",
        remappings=[("~/input/x", "/abs/in"), ("~/output/y", "out")],
    )
    return LaunchDescription([OpaqueFunction(function=launch_setup), relay])
