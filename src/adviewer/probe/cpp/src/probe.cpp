// adviewer live probe (Online mode, D14, FR-8..FR-11).
//
// Observes a running ROS 2 graph and writes probe_dump.json:
//   - every topic with its type(s), publishers and subscribers and each one's QoS
//     (rclcpp::Node::get_publishers_info_by_topic / get_subscriptions_info_by_topic);
//   - per topic over a time window: message count, rate, jitter (std. dev. of the gaps),
//     largest gap, bandwidth and mean message size.
// Rates are measured with generic (serialized) subscriptions: messages are counted, never
// deserialized, so the probe itself stays cheap (NFR-6). The subscriptions are
// best-effort: a best-effort reader receives from reliable and best-effort writers alike,
// and a reliable writer never waits for it. Subscribing still makes DDS deliver the data
// to this process — for large topics use `subscribe:=false` or a narrower `include`.
//
// Parameters: window (s, 5.0), discovery (s, 2.0), output (path), include / exclude
// (regex on topic names), subscribe (bool), loop (bool: rewrite the file every window).

#include <rclcpp/rclcpp.hpp>
#include <rclcpp/version.h>

#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>
#include <regex>
#include <set>
#include <sstream>
#include <string>
#include <vector>

namespace
{
using Clock = std::chrono::steady_clock;

struct Stats
{
  std::mutex mutex;
  std::uint64_t count = 0;
  std::uint64_t bytes = 0;
  Clock::time_point first;
  Clock::time_point last;
  double gap_mean_s = 0.0;  // Welford running mean / M2 of the gaps between messages
  double gap_m2 = 0.0;
  double max_gap_s = 0.0;

  void add(std::size_t size)
  {
    const auto now = Clock::now();
    std::lock_guard<std::mutex> lock(mutex);
    if (count == 0) {
      first = now;
    } else {
      const double gap = std::chrono::duration<double>(now - last).count();
      const double n = static_cast<double>(count);  // gaps seen so far, incl. this one
      const double delta = gap - gap_mean_s;
      gap_mean_s += delta / n;
      gap_m2 += delta * (gap - gap_mean_s);
      if (gap > max_gap_s) {
        max_gap_s = gap;
      }
    }
    last = now;
    ++count;
    bytes += size;
  }
};

std::string escape(const std::string & text)
{
  std::ostringstream out;
  for (const char c : text) {
    switch (c) {
      case '"': out << "\\\""; break;
      case '\\': out << "\\\\"; break;
      case '\n': out << "\\n"; break;
      case '\t': out << "\\t"; break;
      default:
        if (static_cast<unsigned char>(c) < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", c);
          out << buf;
        } else {
          out << c;
        }
    }
  }
  return out.str();
}

std::string quote(const std::string & text) { return "\"" + escape(text) + "\""; }

std::string number(double value)
{
  if (!std::isfinite(value)) {
    return "null";
  }
  std::ostringstream out;
  out.precision(6);
  out << value;
  return out.str();
}

std::string reliability(rclcpp::ReliabilityPolicy p)
{
  switch (p) {
    case rclcpp::ReliabilityPolicy::Reliable: return "reliable";
    case rclcpp::ReliabilityPolicy::BestEffort: return "best_effort";
    case rclcpp::ReliabilityPolicy::SystemDefault: return "system_default";
#if RCLCPP_VERSION_MAJOR >= 21  // BestAvailable came with Iron; Humble (16) builds without it
    case rclcpp::ReliabilityPolicy::BestAvailable: return "best_available";
#endif
    default: return "unknown";
  }
}

std::string durability(rclcpp::DurabilityPolicy p)
{
  switch (p) {
    case rclcpp::DurabilityPolicy::Volatile: return "volatile";
    case rclcpp::DurabilityPolicy::TransientLocal: return "transient_local";
    case rclcpp::DurabilityPolicy::SystemDefault: return "system_default";
#if RCLCPP_VERSION_MAJOR >= 21
    case rclcpp::DurabilityPolicy::BestAvailable: return "best_available";
#endif
    default: return "unknown";
  }
}

std::string history(rclcpp::HistoryPolicy p)
{
  switch (p) {
    case rclcpp::HistoryPolicy::KeepLast: return "keep_last";
    case rclcpp::HistoryPolicy::KeepAll: return "keep_all";
    case rclcpp::HistoryPolicy::SystemDefault: return "system_default";
    default: return "unknown";
  }
}

std::string full_name(const std::string & ns, const std::string & name)
{
  if (ns.empty() || ns == "/") {
    return "/" + name;
  }
  return ns + "/" + name;
}

class Probe : public rclcpp::Node
{
public:
  Probe() : rclcpp::Node("adviewer_probe")
  {
    window_ = declare_parameter<double>("window", 5.0);
    discovery_ = declare_parameter<double>("discovery", 2.0);
    // parameters are declared on the constructed node, in this order (readable --ros-args help)
    // cppcheck-suppress useInitializationList
    output_ = declare_parameter<std::string>("output", "probe_dump.json");
    // cppcheck-suppress useInitializationList
    include_ = std::regex(declare_parameter<std::string>("include", ".*"));
    exclude_ = std::regex(declare_parameter<std::string>("exclude", "^/rosout$|/parameter_events$"));
    subscribe_ = declare_parameter<bool>("subscribe", true);
    loop_ = declare_parameter<bool>("loop", false);
  }

  double window() const { return window_; }
  double discovery() const { return discovery_; }
  bool loop() const { return loop_; }

  // Subscribe to every matching topic not subscribed yet (new topics appear between windows).
  void subscribe_all()
  {
    for (const auto & [topic, types] : get_topic_names_and_types()) {
      if (!std::regex_search(topic, include_) || std::regex_search(topic, exclude_)) {
        continue;
      }
      types_[topic] = types;
      if (!subscribe_ || subs_.count(topic) || skipped_.count(topic) || types.size() != 1) {
        continue;  // a topic with several types cannot be read with one generic subscription
      }
      auto stats = std::make_shared<Stats>();
      const auto qos = rclcpp::QoS(rclcpp::KeepLast(10)).best_effort().durability_volatile();
      try {
        auto sub = create_generic_subscription(
          topic, types.front(), qos,
          [stats](std::shared_ptr<const rclcpp::SerializedMessage> msg) {
            stats->add(msg->size());
          });
        subs_[topic] = sub;
        stats_[topic] = stats;
      } catch (const std::exception & e) {  // type support not installed here: list it, don't measure it
        skipped_.insert(topic);
        RCLCPP_WARN(get_logger(), "not measured %s (%s): %s", topic.c_str(), types.front().c_str(), e.what());
      }
    }
  }

  void reset_stats()
  {
    for (auto & [topic, stats] : stats_) {
      std::lock_guard<std::mutex> lock(stats->mutex);
      stats->count = 0;
      stats->bytes = 0;
      stats->gap_mean_s = 0.0;
      stats->gap_m2 = 0.0;
      stats->max_gap_s = 0.0;
    }
    window_start_ = Clock::now();
  }

  std::string endpoints(const std::vector<rclcpp::TopicEndpointInfo> & infos) const
  {
    std::ostringstream out;
    out << "[";
    bool first = true;
    for (const auto & info : infos) {
      if (info.node_name() == get_name() && info.node_namespace() == get_namespace()) {
        continue;  // the probe's own reader
      }
      const auto & qos = info.qos_profile();
      out << (first ? "" : ", ") << "{\"node\": " << quote(full_name(info.node_namespace(), info.node_name()))
          << ", \"type\": " << quote(info.topic_type()) << ", \"qos\": {\"reliability\": "
          << quote(reliability(qos.reliability())) << ", \"durability\": "
          << quote(durability(qos.durability())) << ", \"history\": " << quote(history(qos.history()))
          << ", \"depth\": " << qos.depth() << "}}";
      first = false;
    }
    out << "]";
    return out.str();
  }

  void write_dump()
  {
    const double elapsed = std::chrono::duration<double>(Clock::now() - window_start_).count();
    std::ostringstream out;
    const auto wall = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    char stamp[32];
    std::strftime(stamp, sizeof(stamp), "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&wall));
    const char * rmw = std::getenv("RMW_IMPLEMENTATION");
    const char * domain = std::getenv("ROS_DOMAIN_ID");
    out << "{\n \"captured_at\": " << quote(stamp) << ",\n \"window_s\": " << number(elapsed)
        << ",\n \"rmw\": " << quote(rmw ? rmw : "") << ",\n \"domain_id\": " << quote(domain ? domain : "0")
        << ",\n \"subscribed\": " << (subscribe_ ? "true" : "false") << ",\n \"nodes\": [";
    bool first = true;
    for (const auto & [name, ns] : get_node_graph_interface()->get_node_names_and_namespaces()) {
      if (name == get_name() && ns == get_namespace()) {
        continue;
      }
      out << (first ? "" : ", ") << quote(full_name(ns, name));
      first = false;
    }
    out << "],\n \"topics\": [";
    first = true;
    for (const auto & [topic, types] : types_) {
      out << (first ? "\n  " : ",\n  ") << "{\"name\": " << quote(topic) << ", \"types\": [";
      for (std::size_t i = 0; i < types.size(); ++i) {
        out << (i ? ", " : "") << quote(types[i]);
      }
      out << "], \"publishers\": " << endpoints(get_publishers_info_by_topic(topic))
          << ", \"subscribers\": " << endpoints(get_subscriptions_info_by_topic(topic));
      const auto it = stats_.find(topic);
      if (it != stats_.end()) {
        auto & s = *it->second;
        std::lock_guard<std::mutex> lock(s.mutex);
        const double span = s.count > 1 ? std::chrono::duration<double>(s.last - s.first).count() : 0.0;
        const double rate = span > 0.0 ? static_cast<double>(s.count - 1) / span : 0.0;
        const double jitter = s.count > 2 ? std::sqrt(s.gap_m2 / static_cast<double>(s.count - 2)) : 0.0;
        out << ", \"measured\": true, \"count\": " << s.count << ", \"rate_hz\": " << number(rate)
            << ", \"jitter_ms\": " << number(jitter * 1000.0) << ", \"max_gap_ms\": "
            << number(s.max_gap_s * 1000.0) << ", \"bandwidth_Bps\": "
            << number(elapsed > 0.0 ? static_cast<double>(s.bytes) / elapsed : 0.0)
            << ", \"mean_size_B\": " << number(s.count ? static_cast<double>(s.bytes) / s.count : 0.0);
      } else {
        out << ", \"measured\": false";
      }
      out << "}";
      first = false;
    }
    out << "\n ]\n}\n";
    const std::string tmp = output_ + ".tmp";
    std::ofstream(tmp) << out.str();
    std::rename(tmp.c_str(), output_.c_str());  // readers never see a half-written file
    RCLCPP_INFO(get_logger(), "wrote %s: %zu topics, window %.1f s", output_.c_str(), types_.size(), elapsed);
  }

private:
  double window_{5.0};
  double discovery_{2.0};
  std::string output_;
  std::regex include_;
  std::regex exclude_;
  bool subscribe_{true};
  bool loop_{false};
  Clock::time_point window_start_{Clock::now()};
  std::map<std::string, std::vector<std::string>> types_;
  std::map<std::string, std::shared_ptr<Stats>> stats_;
  std::map<std::string, rclcpp::GenericSubscription::SharedPtr> subs_;
  std::set<std::string> skipped_;  // topics whose message type cannot be loaded in this image
};

void spin_for(rclcpp::Executor & executor, double seconds)
{
  const auto until = Clock::now() + std::chrono::duration<double>(seconds);
  while (rclcpp::ok() && Clock::now() < until) {
    executor.spin_some(std::chrono::milliseconds(50));
  }
}
}  // namespace

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto probe = std::make_shared<Probe>();
  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(probe);
  spin_for(executor, probe->discovery());  // let DDS discovery settle
  do {
    probe->subscribe_all();
    probe->reset_stats();
    spin_for(executor, probe->window());
    if (rclcpp::ok()) {
      probe->write_dump();
    }
  } while (probe->loop() && rclcpp::ok());
  rclcpp::shutdown();
  return 0;
}
