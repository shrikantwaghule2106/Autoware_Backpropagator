from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction
from launch.conditions import IfCondition, LaunchConfigurationEquals, UnlessCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import ComposableNodeContainer, Node
from launch_ros.descriptions import ComposableNode


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("use_a", default_value="false"),
        DeclareLaunchArgument("mode", default_value="fast"),
        Node(package="cond_pkg", executable="a", name="py_a",
             condition=IfCondition(LaunchConfiguration("use_a"))),
        Node(package="cond_pkg", executable="b", name="py_b",
             condition=UnlessCondition(LaunchConfiguration("use_a"))),
        Node(package="cond_pkg", executable="f", name="py_fast",
             condition=LaunchConfigurationEquals("mode", "fast")),
        Node(package="cond_pkg", executable="e", name="py_expr",
             condition=IfCondition(PythonExpression(["'", LaunchConfiguration("mode"), "' == 'slow'"]))),
        ComposableNodeContainer(
            name="container", package="rclcpp_components", executable="component_container",
            condition=IfCondition(LaunchConfiguration("use_a")),
            composable_node_descriptions=[
                ComposableNode(package="cond_pkg", plugin="X", name="py_in_container"),
            ],
        ),
        GroupAction([Node(package="cond_pkg", executable="g", name="py_grouped")],
                    condition=UnlessCondition("true")),
    ])
