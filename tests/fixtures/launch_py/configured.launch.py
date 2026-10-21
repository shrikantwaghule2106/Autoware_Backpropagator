"""Test fixture: values that come from launch arguments (never executed)."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.descriptions import ComposableNode


def launch_setup(context):
    local_topic = LaunchConfiguration("input_topic").perform(context)
    return [
        ComposableNode(
            package="demo_filters",
            plugin="demo::Filter",
            name=LaunchConfiguration("filter_name"),
            remappings=[
                ("input", LaunchConfiguration("input_topic")),
                ("output", LaunchConfiguration("output_prefix").perform(context) + "/filtered"),
                ("~/output/debug", [LaunchConfiguration("output_prefix"), "/debug"]),
                ("~/input/aux", local_topic),
            ],
        ),
    ]


def generate_launch_description():
    launch_arguments = []

    def add_launch_arg(name, default_value=None):
        launch_arguments.append(DeclareLaunchArgument(name, default_value=default_value))

    add_launch_arg("filter_name", "my_filter")
    add_launch_arg("input_topic", "raw")
    return LaunchDescription(
        [
            *launch_arguments,
            DeclareLaunchArgument("output_prefix", default_value="out"),
            OpaqueFunction(function=launch_setup),
        ]
    )
