// F7b: aliases declared in a header of the same package
#include <autoware_vehicle_msgs/msg/velocity_report.hpp>
namespace vel_conv
{
using VelocityReport = autoware_vehicle_msgs::msg::VelocityReport;
typedef geometry_msgs::msg::PoseStamped PoseAlias;
using Clash = std_msgs::msg::Bool;
}
