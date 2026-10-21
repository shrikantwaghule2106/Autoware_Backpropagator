sub_ = create_subscription<sensor_msgs::msg::PointCloud2>(input_topics_[d], qos, cb);
twist_ = this->create_subscription<geometry_msgs::msg::TwistWithCovarianceStamped>("~/input/twist", 10, cb);
pub_ = create_publisher<std_msgs::msg::String>(name_param_, 1);
sub2_ = create_subscription<std_msgs::msg::String>("~/input/text", 1, cb);
