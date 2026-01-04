import logging
import datetime
import homeassistant.util.dt as dt_util
from homeassistant.helpers.entity import Entity
from homeassistant.helpers import storage
from homeassistant.util import Throttle
import aiohttp
from .const import (
    DOMAIN,
    DATA_CACHE_KEY,
    ATTRIBUTES
)

_LOGGER = logging.getLogger(__name__)
MIN_TIME_BETWEEN_UPDATES = datetime.timedelta(hours=6)

class HolidayCountdownSensor(Entity):
    def __init__(self, hass):
        self._hass = hass
        self._state = False
        self._attributes = {
            ATTRIBUTES['name']: "加载中...",
            ATTRIBUTES['days']: None,
            ATTRIBUTES['countdown']: None,
            ATTRIBUTES['date']: None,
            ATTRIBUTES['next']: None
        }
        self._unique_id = "holiday_sensor_cn"
        self._store = storage.Store(hass, 1, DATA_CACHE_KEY)
        self._last_updated = None
        self._holidays = []

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def name(self):
        return "节假日倒计时"

    @property
    def state(self):
        return "on" if self._state else "off"

    @property
    def icon(self):
        return "mdi:calendar-check" if self._state else "mdi:calendar-outline"

    @property
    def extra_state_attributes(self):
        return self._attributes

    async def async_added_to_hass(self):
        await self._load_cached_data()
        if not self._holidays:
            await self._update_holiday_data()
        else:
            self._process_next_holiday()
    
    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def async_update(self):
        await self._update_holiday_data()
    
    async def _load_cached_data(self):
        try:
            cached_data = await self._store.async_load()
            if cached_data and isinstance(cached_data, dict):
                self._holidays = []
                for holiday_data in cached_data.get('holidays', []):
                    try:
                        date = datetime.datetime.fromisoformat(holiday_data['date']).date()
                        self._holidays.append({
                            'date': date,
                            'name': holiday_data['name'],
                            'duration': holiday_data['duration']
                        })
                    except Exception as e:
                        _LOGGER.error("加载节假日数据失败: %s", e)
                
                last_updated_str = cached_data.get('last_updated')
                if last_updated_str:
                    self._last_updated = dt_util.as_local(dt_util.parse_datetime(last_updated_str))
                
                _LOGGER.debug("已加载缓存数据：%s 个节假日", len(self._holidays))
                
                if self._holidays:
                    self._process_next_holiday()
        except Exception as e:
            _LOGGER.error("加载缓存数据失败: %s", e)
    
    async def _update_holiday_data(self):
        """更新节假日数据"""
        try:
            now = dt_util.now()
            current_year = now.year
            
            need_update = not self._last_updated or now.date() > self._last_updated.date()
            if self._last_updated and (now.date() - self._last_updated.date()).days > 30:
                need_update = True
            
            if need_update:
                _LOGGER.debug("需要更新节假日数据")
                
                # 获取当年节假日数据
                url = f"https://timor.tech/api/holiday/year/{current_year}"
                _LOGGER.debug("请求URL: %s", url)
                
                try:
                    headers = {'User-Agent': 'HomeAssistant/1.0'}
                    _LOGGER.debug("开始请求节假日数据，URL: %s", url)
                    
                    async with aiohttp.ClientSession() as session:
                        try:
                            _LOGGER.debug("发送HTTP请求...")
                            async with session.get(url, headers=headers, timeout=10) as response:
                                _LOGGER.debug("收到响应，状态码: %d", response.status)
                                _LOGGER.debug("响应头: %s", dict(response.headers))
                                
                                response.raise_for_status()
                                _LOGGER.debug("HTTP请求成功，开始解析JSON数据...")
                                
                                try:
                                    data = await response.json()
                                    _LOGGER.debug("JSON解析成功，数据结构: %s", type(data))
                                    _LOGGER.debug("完整响应数据: %s", data)
                                except Exception as json_error:
                                    _LOGGER.error("JSON解析失败，原始响应: %s", await response.text())
                                    _LOGGER.error("JSON解析错误详情: %s", json_error)
                                    raise
                                
                                if data.get('code') == 0:
                                    holiday_data = data.get('holiday', {})
                                    _LOGGER.debug("节假日数据: %s", holiday_data)
                                    
                                    self._holidays = self._parse_holiday_data(holiday_data, current_year)
                                    _LOGGER.info("获取 %d 年节假日成功，共 %d 个节假日", current_year, len(self._holidays))
                                    
                                    # 检查当年是否有有效节假日，如果没有则获取下一年的数据
                                    if not self._has_valid_holidays(self._holidays):
                                        _LOGGER.info("当年无有效节假日，尝试获取下一年的节假日数据")
                                        _LOGGER.debug("当前节假日列表: %s", self._holidays)
                                        try:
                                            next_year_holidays = await self._get_next_year_holidays(current_year + 1)
                                            if next_year_holidays:
                                                self._holidays.extend(next_year_holidays)
                                                _LOGGER.info("获取下一年的节假日数据成功，共 %d 个节假日", len(next_year_holidays))
                                            else:
                                                _LOGGER.warning("获取下一年节假日数据失败，将使用当年数据")
                                        except Exception as next_year_error:
                                            _LOGGER.warning("获取下一年节假日数据时出错: %s，将使用当年数据", next_year_error)
                                    
                                    if self._holidays:  # 确保有数据才保存
                                        _LOGGER.debug("准备保存节假日数据到缓存...")
                                        await self._store.async_save({
                                            'holidays': [{
                                                'date': holiday['date'].isoformat(),
                                                'name': holiday['name'],
                                                'duration': holiday['duration']
                                            } for holiday in self._holidays],
                                            'last_updated': now.isoformat()
                                        })
                                        self._last_updated = now
                                        _LOGGER.info("节假日数据已缓存")
                                        
                                        self._process_next_holiday()
                                    else:
                                        _LOGGER.warning("没有获取到任何节假日数据")
                                else:
                                    error_msg = data.get('msg', '未知错误')
                                    error_code = data.get('code', '未知错误码')
                                    _LOGGER.error("API返回错误 - 错误码: %s, 错误信息: %s", error_code, error_msg)
                                    _LOGGER.error("完整错误响应: %s", data)
                        except aiohttp.ClientResponseError as response_error:
                            _LOGGER.error("HTTP响应错误 - 状态码: %d, 状态: %s", response_error.status, response_error.message)
                            _LOGGER.error("请求URL: %s", response_error.request_info.url if response_error.request_info else "未知")
                            raise
                except aiohttp.ClientConnectorError as connector_error:
                    _LOGGER.error("网络连接失败: %s", connector_error)
                    _LOGGER.error("可能的原因: 网络不可达、DNS解析失败或服务器拒绝连接")
                except aiohttp.ClientTimeout as timeout_error:
                    _LOGGER.error("请求超时: %s", timeout_error)
                    _LOGGER.error("可能的原因: 网络延迟过高或服务器响应缓慢")
                except aiohttp.ClientError as e:
                    _LOGGER.error("网络请求失败: %s", e)
                    _LOGGER.error("错误类型: %s", type(e).__name__)
                except ValueError as e:
                    _LOGGER.error("JSON解析失败: %s", e)
                    _LOGGER.error("可能的原因: API返回的不是有效的JSON格式")
                except Exception as e:
                    _LOGGER.error("获取节假日数据失败: %s", e, exc_info=True)
                    _LOGGER.error("错误类型: %s", type(e).__name__)
        except Exception as e:
            _LOGGER.error("更新节假日数据出错: %s", e, exc_info=True)
            self._state = False
            self._attributes[ATTRIBUTES['name']] = f"错误: {str(e)}"
    
    def _parse_holiday_data(self, holiday_data, year):
        """解析节假日数据，自动补全年份"""
        holidays = []
        today = dt_util.now().date()
        
        _LOGGER.debug("开始解析 %d 年的节假日数据", year)
        _LOGGER.debug("原始节假日数据类型: %s", type(holiday_data))
        _LOGGER.debug("原始节假日数据长度: %d", len(holiday_data) if holiday_data else 0)
        
        if not holiday_data or not isinstance(holiday_data, dict):
            _LOGGER.warning("节假日数据为空或格式无效: %s", holiday_data)
            return []
        
        processed_count = 0
        skipped_count = 0
        
        for date_str, info in holiday_data.items():
            try:
                _LOGGER.debug("处理日期: %s, 信息: %s", date_str, info)
                
                if not info or not isinstance(info, dict):
                    _LOGGER.debug("跳过无效的节假日信息: %s - %s", date_str, info)
                    skipped_count += 1
                    continue
                    
                if info.get('holiday'):
                    # 处理日期格式
                    original_date_str = date_str
                    if '-' in date_str:
                        parts = date_str.split('-')
                        if len(parts) == 2:
                            date_str = f"{year}-{date_str}"
                        elif len(parts) == 3 and len(parts[0]) != 4:
                            date_str = f"{year}-{parts[1]}-{parts[2]}"
                    
                    _LOGGER.debug("日期格式处理: %s -> %s", original_date_str, date_str)
                    
                    try:
                        date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
                    except ValueError as date_error:
                        _LOGGER.warning("日期格式无效 '%s': %s", date_str, date_error)
                        skipped_count += 1
                        continue
                    
                    days_diff = (date_obj - today).days
                    _LOGGER.debug("日期 %s 与今天相差: %d 天", date_obj, days_diff)
                    
                    if days_diff >= -30:
                        holiday_name = info.get('name', '未知节日')
                        holidays.append({
                            'date': date_obj,
                            'name': holiday_name,
                            'original_name': holiday_name
                        })
                        processed_count += 1
                        _LOGGER.debug("添加节假日: %s (%s), 相差 %d 天", holiday_name, date_obj, days_diff)
                    else:
                        _LOGGER.debug("跳过过期节假日: %s (%s), 相差 %d 天", 
                                     info.get('name', '未知节日'), date_obj, days_diff)
                        skipped_count += 1
                else:
                    _LOGGER.debug("非节假日日期: %s", date_str)
                    skipped_count += 1
            except Exception as e:
                _LOGGER.warning("解析节假日 '%s' 失败: %s", date_str, e, exc_info=True)
                skipped_count += 1
        
        _LOGGER.info("解析完成 - 年份: %d, 处理: %d, 跳过: %d, 最终节假日: %d", 
                    year, processed_count, skipped_count, len(holidays))
        
        grouped = {}
        for holiday in holidays:
            base_name = self._get_base_holiday_name(holiday['name'])
            if base_name not in grouped:
                grouped[base_name] = []
            grouped[base_name].append(holiday)
        
        result = []
        for base_name, items in grouped.items():
            sorted_dates = sorted(items, key=lambda x: x['date'])
            result.append({
                'name': base_name,
                'date': sorted_dates[0]['date'],
                'duration': len(sorted_dates),
                'all_dates': sorted_dates
            })
        
        return sorted(result, key=lambda x: x['date'])
    
    def _get_base_holiday_name(self, name):
        for suffix in ["(1月", "(2月", "(3月", "(4月", "(5月", "(6月", 
                     "(7月", "(8月", "(9月", "(10月", "(11月", "(12月"]:
            pos = name.find(suffix)
            if pos > 0:
                return name[:pos].strip()
        return name
    
    def _has_valid_holidays(self, holidays):
        """检查是否有有效的节假日（未来的节假日）"""
        try:
            today = dt_util.now().date()
            for holiday in holidays:
                if holiday['date'] >= today:
                    return True
            return False
        except Exception as e:
            _LOGGER.error("检查节假日有效性失败: %s", e)
            return False
    
    async def _get_next_year_holidays(self, year):
        """获取下一年的节假日数据"""
        try:
            url = f"https://timor.tech/api/holiday/year/{year}"
            _LOGGER.info("请求下一年节假日数据，URL: %s", url)
            
            headers = {'User-Agent': 'HomeAssistant/1.0'}
            async with aiohttp.ClientSession() as session:
                try:
                    _LOGGER.debug("开始请求下一年节假日数据...")
                    async with session.get(url, headers=headers, timeout=10) as response:
                        _LOGGER.debug("下一年数据响应状态码: %d", response.status)
                        response.raise_for_status()
                        
                        try:
                            data = await response.json()
                            _LOGGER.debug("下一年数据JSON解析成功")
                            _LOGGER.debug("下一年完整响应: %s", data)
                        except Exception as json_error:
                            _LOGGER.error("下一年数据JSON解析失败，原始响应: %s", await response.text())
                            raise
                        
                        if data.get('code') == 0:
                            holiday_data = data.get('holiday', {})
                            _LOGGER.debug("下一年节假日原始数据: %s", holiday_data)
                            
                            holidays = self._parse_holiday_data(holiday_data, year)
                            _LOGGER.info("成功获取 %d 年节假日数据: %d 个", year, len(holidays))
                            _LOGGER.debug("解析后的节假日: %s", holidays)
                            return holidays
                        else:
                            error_msg = data.get('msg', '未知错误')
                            error_code = data.get('code', '未知错误码')
                            _LOGGER.warning("下一年数据API返回错误 - 错误码: %s, 错误信息: %s", error_code, error_msg)
                            _LOGGER.warning("下一年数据完整错误响应: %s", data)
                            return []
                except aiohttp.ClientResponseError as response_error:
                    _LOGGER.error("下一年数据HTTP响应错误 - 状态码: %d, 状态: %s", response_error.status, response_error.message)
                    raise
        except aiohttp.ClientConnectorError as connector_error:
            _LOGGER.warning("下一年数据网络连接失败: %s", connector_error)
        except aiohttp.ClientTimeout as timeout_error:
            _LOGGER.warning("下一年数据请求超时: %s", timeout_error)
        except aiohttp.ClientError as e:
            _LOGGER.warning("获取下一年节假日数据网络请求失败: %s", e)
        except ValueError as e:
            _LOGGER.warning("获取下一年节假日数据JSON解析失败: %s", e)
        except Exception as e:
            _LOGGER.warning("获取下一年节假日数据失败: %s", e)
        
        return []
    
    def _process_next_holiday(self):
        try:
            today = dt_util.now().date()
            current_holiday = None
            next_holiday = None
            
            # 检查是否有正在进行的节假日（今天在节假日日期范围内）
            for holiday in self._holidays:
                holiday_start = holiday['date']
                holiday_end = holiday['date'] + datetime.timedelta(days=holiday['duration'] - 1)
                
                if holiday_start <= today <= holiday_end:
                    current_holiday = holiday
                    _LOGGER.info("今天是节假日: %s (开始: %s, 结束: %s, 持续: %d 天)",
                                holiday['name'], holiday_start, holiday_end, holiday['duration'])
                    break
            
            if current_holiday:
                # 今天是节假日
                holiday_end = current_holiday['date'] + datetime.timedelta(days=current_holiday['duration'] - 1)
                days_remaining = (holiday_end - today).days
                
                self._state = True
                self._attributes = {
                    ATTRIBUTES['name']: current_holiday['name'],
                    ATTRIBUTES['days']: current_holiday['duration'],
                    ATTRIBUTES['countdown']: f"剩余 {days_remaining} 天",
                    ATTRIBUTES['date']: current_holiday['date'].isoformat(),
                    ATTRIBUTES['next']: self._get_next_holiday(current_holiday) or "无"
                }
                _LOGGER.debug("节假日进行中 - 名称: %s, 剩余天数: %d", current_holiday['name'], days_remaining)
            else:
                # 今天不是节假日，查找下一个节假日
                for holiday in self._holidays:
                    if holiday['date'] > today:
                        next_holiday = holiday
                        break
                
                self._state = False
                
                if next_holiday:
                    days_left = (next_holiday['date'] - today).days
                    
                    self._attributes = {
                        ATTRIBUTES['name']: "非节假日",
                        ATTRIBUTES['days']: next_holiday['duration'],
                        ATTRIBUTES['countdown']: f"距离 {next_holiday['name']} 还有 {days_left} 天",
                        ATTRIBUTES['date']: next_holiday['date'].isoformat(),
                        ATTRIBUTES['next']: self._get_next_holiday(next_holiday) or "无"
                    }
                    _LOGGER.debug("下一个节假日 - 名称: %s, 倒计时: %d 天", next_holiday['name'], days_left)
                else:
                    self._attributes = {
                        ATTRIBUTES['name']: "非节假日",
                        ATTRIBUTES['days']: 0,
                        ATTRIBUTES['countdown']: "今年无更多节假日",
                        ATTRIBUTES['date']: None,
                        ATTRIBUTES['next']: None
                    }
                    _LOGGER.info("没有找到未来的节假日")
        except Exception as e:
            _LOGGER.error("处理节假日数据失败: %s", e, exc_info=True)
            self._state = False
            self._state = "数据处理错误"
            self._attributes[ATTRIBUTES['name']] = f"处理错误: {str(e)}"
    
    def _get_next_holiday(self, current_holiday):
        try:
            today = dt_util.now().date()
            current_holiday_start = current_holiday['date']
            current_holiday_end = current_holiday_start + datetime.timedelta(days=current_holiday['duration'] - 1)
            
            for holiday in self._holidays:
                holiday_start = holiday['date']
                # 找到在当前节假日结束之后的第一个节假日
                if holiday_start > current_holiday_end:
                    return holiday['name']
        except Exception as e:
            _LOGGER.error("获取下一个节假日失败: %s", e)
        return None

async def async_setup_entry(hass, config_entry, async_add_entities):
    sensor = HolidayCountdownSensor(hass)
    async_add_entities([sensor], True)