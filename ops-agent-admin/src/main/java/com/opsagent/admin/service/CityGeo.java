package com.opsagent.admin.service;

import java.util.Map;

/**
 * 主要城市经纬度映射，供天气采集定位使用。
 */
public final class CityGeo {

    private CityGeo() {}

    public static final Map<String, double[]> CITIES = Map.ofEntries(
            Map.entry("北京", new double[]{39.9042, 116.4074}),
            Map.entry("上海", new double[]{31.2304, 121.4737}),
            Map.entry("广州", new double[]{23.1291, 113.2644}),
            Map.entry("深圳", new double[]{22.5431, 114.0579}),
            Map.entry("成都", new double[]{30.5728, 104.0668}),
            Map.entry("杭州", new double[]{30.2741, 120.1551}),
            Map.entry("武汉", new double[]{30.5928, 114.3055}),
            Map.entry("西安", new double[]{34.3416, 108.9398}),
            Map.entry("南京", new double[]{32.0603, 118.7969}),
            Map.entry("重庆", new double[]{29.5630, 106.5516}),
            Map.entry("天津", new double[]{39.3434, 117.3616}),
            Map.entry("苏州", new double[]{31.2989, 120.5853}),
            Map.entry("长沙", new double[]{28.2282, 112.9388}),
            Map.entry("郑州", new double[]{34.7466, 113.6254}),
            Map.entry("青岛", new double[]{36.0671, 120.3826}),
            Map.entry("沈阳", new double[]{41.8057, 123.4315}),
            Map.entry("大连", new double[]{38.9140, 121.6147}),
            Map.entry("厦门", new double[]{24.4798, 118.0894}),
            Map.entry("昆明", new double[]{24.8801, 102.8329}),
            Map.entry("哈尔滨", new double[]{45.8038, 126.5349})
    );

    public static double[] get(String city) {
        return CITIES.getOrDefault(city, new double[]{39.9042, 116.4074});
    }

    public static java.util.List<String> cities() {
        return new java.util.ArrayList<>(CITIES.keySet());
    }
}
