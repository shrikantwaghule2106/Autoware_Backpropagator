// Test code publishes what the node subscribes to; it must not make the direction ambiguous.
auto pub = node->create_publisher<autoware_vehicle_msgs::msg::VelocityReport>("velocity_status", 1);
