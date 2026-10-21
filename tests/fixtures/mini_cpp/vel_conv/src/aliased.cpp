#include "vel_conv/types.hpp"
using Clash = std_msgs::msg::String;  // same alias, other type: not resolved
void make(rclcpp::Node & node)
{
  auto a = node.create_publisher<vel_conv::VelocityReport>("~/output/aliased", rclcpp::SensorDataQoS().keep_last(1));
  auto b = node.create_subscription<PoseAlias>("~/input/pose", rclcpp::QoS(rclcpp::KeepLast(3)).best_effort(), cb);
  auto c = node.create_subscription<Clash>("~/input/clash", qos, cb);
}
