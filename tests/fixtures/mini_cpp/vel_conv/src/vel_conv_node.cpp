#include <rclcpp/rclcpp.hpp>

VelConv::VelConv(const rclcpp::NodeOptions & options) : Node("vel_conv", options)
{
  sub_ = create_subscription<autoware_vehicle_msgs::msg::VelocityReport>(
    "velocity_status", rclcpp::QoS{10}, std::bind(&VelConv::on_velocity, this, std::placeholders::_1));
  pub_ = create_publisher<geometry_msgs::msg::TwistWithCovarianceStamped>(
    "twist_with_covariance", rclcpp::QoS{10});
}
