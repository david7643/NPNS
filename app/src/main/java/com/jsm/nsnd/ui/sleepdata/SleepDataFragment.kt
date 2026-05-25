package com.jsm.nsnd.ui.sleepdata

import android.app.DatePickerDialog
import android.graphics.Color
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import com.github.mikephil.charting.components.XAxis
import com.github.mikephil.charting.data.Entry
import com.github.mikephil.charting.data.LineData
import com.github.mikephil.charting.data.LineDataSet
import com.github.mikephil.charting.formatter.ValueFormatter
import com.jsm.nsnd.R
import com.jsm.nsnd.databinding.FragmentSleepDataBinding
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

class SleepDataFragment : Fragment() {

    private var _binding: FragmentSleepDataBinding? = null
    private val binding get() = _binding!!

    private var selectedCalendar = Calendar.getInstance()

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentSleepDataBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        setupDateDisplay()
        setupCalendarButton()
        setupChart()
        setupRecyclerView()
    }

    // ─────────────────────────────────────────
    // 날짜 표시
    // ─────────────────────────────────────────
    private fun setupDateDisplay() {
        updateDateText()
    }

    private fun updateDateText() {
        val today = Calendar.getInstance()
        val isToday = selectedCalendar.get(Calendar.YEAR) == today.get(Calendar.YEAR) &&
                selectedCalendar.get(Calendar.DAY_OF_YEAR) == today.get(Calendar.DAY_OF_YEAR)

        val sdf = SimpleDateFormat("yyyy년 M월 d일", Locale.KOREAN)
        val dateStr = sdf.format(selectedCalendar.time)
        binding.tvDate.text = if (isToday) "$dateStr (오늘)" else dateStr
    }

    // ─────────────────────────────────────────
    // 캘린더 버튼
    // ─────────────────────────────────────────
    private fun setupCalendarButton() {
        binding.btnCalendar.setOnClickListener {
            val year = selectedCalendar.get(Calendar.YEAR)
            val month = selectedCalendar.get(Calendar.MONTH)
            val day = selectedCalendar.get(Calendar.DAY_OF_MONTH)

            DatePickerDialog(requireContext(), { _, y, m, d ->
                selectedCalendar.set(y, m, d)
                updateDateText()
                // TODO: 젯슨 나노 서버에서 선택한 날짜 데이터 조회로 교체
                loadDummyData()
            }, year, month, day).show()
        }
    }

    // ─────────────────────────────────────────
    // 그래프 설정
    // ─────────────────────────────────────────
    private fun setupChart() {
        // TODO: 젯슨 나노에서 실제 수면 데이터 수신으로 교체
        val dummyEntries = listOf(
            Entry(8f, 0f),
            Entry(9f, 1f),
            Entry(10f, 0f),
            Entry(11f, 2f),
            Entry(12f, 1f),
            Entry(13f, 0f),
            Entry(14f, 3f),
            Entry(15f, 2f),
            Entry(16f, 0f)
        )

        val dataSet = LineDataSet(dummyEntries, "수면 단계").apply {
            color = requireContext().getColor(R.color.accent_primary)
            setCircleColor(requireContext().getColor(R.color.accent_light))
            lineWidth = 2f
            circleRadius = 4f
            setDrawValues(false)
            mode = LineDataSet.Mode.STEPPED
            fillColor = requireContext().getColor(R.color.accent_secondary)
            fillAlpha = 80
            setDrawFilled(true)
        }

        binding.lineChart.apply {
            data = LineData(dataSet)
            description.isEnabled = false
            legend.isEnabled = false
            setBackgroundColor(Color.TRANSPARENT)
            setTouchEnabled(false)

            // X축 설정
            xAxis.apply {
                position = XAxis.XAxisPosition.BOTTOM
                textColor = requireContext().getColor(R.color.text_hint)
                textSize = 10f
                gridColor = requireContext().getColor(R.color.divider)
                axisLineColor = requireContext().getColor(R.color.divider)
                granularity = 1f
                valueFormatter = object : ValueFormatter() {
                    override fun getFormattedValue(value: Float) = "${value.toInt()}시"
                }
            }

            // Y축 설정
            axisLeft.apply {
                textColor = requireContext().getColor(R.color.text_hint)
                textSize = 10f
                gridColor = requireContext().getColor(R.color.divider)
                axisLineColor = requireContext().getColor(R.color.divider)
                axisMinimum = 0f
                axisMaximum = 3.5f
                granularity = 1f
                valueFormatter = object : ValueFormatter() {
                    override fun getFormattedValue(value: Float) =
                        if (value == 0f) "정상" else "${value.toInt()}단계"
                }
            }
            axisRight.isEnabled = false
            invalidate()
        }
    }

    // ─────────────────────────────────────────
    // 수면 이벤트 RecyclerView
    // ─────────────────────────────────────────
    private fun setupRecyclerView() {
        loadDummyData()
    }

    private fun loadDummyData() {
        val dummyEvents = listOf(
            SleepEventItem("09:38", "서울 강남구", 1),
            SleepEventItem("09:21", "서울 서초구", 2),
            SleepEventItem("08:55", "경기 성남시", 3)
        )

        if (dummyEvents.isEmpty()) {
            binding.tvSleepEmpty.visibility = View.VISIBLE
            binding.rvSleepEvents.visibility = View.GONE
        } else {
            binding.tvSleepEmpty.visibility = View.GONE
            binding.rvSleepEvents.visibility = View.VISIBLE
            binding.rvSleepEvents.apply {
                layoutManager = LinearLayoutManager(requireContext())
                adapter = SleepEventAdapter(dummyEvents)
                isNestedScrollingEnabled = false  // NestedScrollView 안에서 RecyclerView 스크롤 충돌 방지
            }
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}