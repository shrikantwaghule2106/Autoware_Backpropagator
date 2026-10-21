"""Test fixture (F11c): the shapes of sample_sensor_kit_launch/pointcloud_preprocessor.launch.py."""
import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.descriptions import ComposableNode, ParameterFile
from launch_ros.substitutions import FindPackageShare


def launch_setup(context):
    param = ParameterFile(
        param_file=LaunchConfiguration("concat_param_path").perform(context), allow_substs=True
    )
    prefix = LaunchConfiguration("prefix").perform(context)
    twice = "/first"
    twice = "/second"
    nodes = [
        ComposableNode(
            package="f11c_pkg", plugin="X", name="concatenate_data",
            remappings=[("output", f"{prefix}/concatenated"), ("~/input/twice", twice)],
            parameters=[param],
        ),
        ComposableNode(
            package="f11c_pkg", plugin="Y", name="joined",
            parameters=[
                PathJoinSubstitution([FindPackageShare("f11c_pkg"), "config", "concat.param.yaml"]),
                os.path.join(FindPackageShare("f11c_pkg").perform(context), "config", "missing.yaml"),
            ],
        ),
    ]
    for i in range(2):
        loop_name = f"looped_{i}"
        nodes.append(ComposableNode(package="f11c_pkg", plugin="Z", name=loop_name))
    return nodes


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("concat_param_path"),
        DeclareLaunchArgument("prefix", default_value="/sensing/lidar"),
        OpaqueFunction(function=launch_setup),
    ])
