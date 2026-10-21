#include <rclcpp/rclcpp.hpp>

DemoNode::DemoNode(const rclcpp::NodeOptions & options) : Node("demo_node", options)
{
  sub_velocity_ = create_subscription<autoware_vehicle_msgs::msg::VelocityReport>(
    "velocity_status", rclcpp::QoS{10},
    std::bind(&DemoNode::on_velocity, this, std::placeholders::_1));
  pub_twist_ = this->create_publisher<geometry_msgs::msg::TwistWithCovarianceStamped>(
    "twist_with_covariance", rclcpp::QoS(1).transient_local());
  pub_debug_ = create_publisher<DebugMsg>("~/debug/value", rclcpp::SensorDataQoS());
  const std::string topic = "computed";
  sub_other_ = create_subscription<std_msgs::msg::String>(topic, 1, cb);
}
